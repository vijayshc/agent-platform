"""Knowledge base question answering, reranking, context assembly, and citations."""

import logging
import os
from typing import Any, Dict, List, Optional

from src.utils import collection_access, knowledge_ingest
from src.utils.browser_llm_proxy import is_browser_llm_latest_message_only

logger = logging.getLogger("text2sql.knowledge_qa")


def search_across_collections(
    manager,
    query_embedding,
    target_doc_ids: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Vector-search every collection that holds a target document."""
    cursor = manager.conn.cursor()
    cursor.execute('SELECT id, collection_name FROM knowledge_documents')
    collection_by_doc = {
        str(row[0]): (row[1] or collection_access.DEFAULT_KNOWLEDGE_COLLECTION)
        for row in cursor.fetchall()
    }
    if target_doc_ids is None:
        targets = list(collection_by_doc)
    else:
        targets = [str(doc_id) for doc_id in target_doc_ids if str(doc_id) in collection_by_doc]

    grouped: Dict[str, List[str]] = {}
    for doc_id in targets:
        grouped.setdefault(collection_by_doc[doc_id], []).append(doc_id)

    hits: List[Dict[str, Any]] = []
    per_collection = 20
    for collection, doc_ids in grouped.items():
        filter_expr = (
            {"document_id": doc_ids[0]} if len(doc_ids) == 1
            else {"document_id": {"$in": doc_ids}}
        )
        try:
            hits.extend(manager.vector_store.search_similar(
                collection,
                query_embedding,
                limit=per_collection,
                output_fields=['document_id', 'chunk_id', 'query_text'],
                filter_expr=filter_expr
            ))
        except Exception as e:
            manager.logger.info("Search failed in collection %s: %s", collection, str(e), exc_info=True)

    hits.sort(key=lambda hit: hit.get('similarity') or 0, reverse=True)
    return hits[:60]


def get_answer(
    manager,
    query: str,
    user_id: int,
    stream: bool = False,
    tags: Optional[List[str]] = None,
    conversation_history: Optional[List[Dict[str, str]]] = None,
    user_roles: Optional[List[str]] = None,
):
    """Get an answer to a query using the knowledge base."""
    try:
        query_embedding = manager._get_embedding(query)
        allowed_doc_ids = manager._retrievable_document_ids(user_id, user_roles)
        manager.logger.info(
            "Allowed document IDs for retrieval: %s",
            len(allowed_doc_ids) if allowed_doc_ids is not None else 'ALL'
        )

        tag_filtered_doc_ids = None
        if tags and isinstance(tags, list) and len(tags) > 0:
            manager.logger.info("Filtering documents by tags: %s", tags)
            tag_filtered_doc_ids = manager._get_document_ids_by_tags(tags)
            manager.logger.info("Filtered document IDs by tags: %s", tag_filtered_doc_ids)

            if not tag_filtered_doc_ids:
                manager.logger.info("No documents found with tags: %s", tags)
                if stream:
                    def empty_generator():
                        yield "No documents found with the selected tags."
                    return empty_generator(), []
                else:
                    return {
                        'success': False,
                        'answer': 'No documents found with the selected tags.',
                        'sources': []
                    }

        final_filtered_ids = None
        if allowed_doc_ids is not None and tag_filtered_doc_ids is not None:
            final_filtered_ids = list(set(allowed_doc_ids) & set(tag_filtered_doc_ids))
            if not final_filtered_ids:
                msg = "No documents found matching both tags and your access permissions."
                if stream:
                    def empty_generator():
                        yield msg
                    return empty_generator(), []
                else:
                    return {'success': False, 'answer': msg, 'sources': []}
        elif allowed_doc_ids is not None:
            final_filtered_ids = allowed_doc_ids
            if not final_filtered_ids:
                msg = "You don't have access to any documents in the knowledge base."
                if stream:
                    def empty_generator():
                        yield msg
                    return empty_generator(), []
                else:
                    return {'success': False, 'answer': msg, 'sources': []}
        elif tag_filtered_doc_ids is not None:
            final_filtered_ids = tag_filtered_doc_ids

        top_chunks = manager._search_across_collections(query_embedding, final_filtered_ids)

        if not top_chunks:
            msg = 'No relevant information found in the knowledge base.'
            if stream:
                def empty_generator():
                    yield msg
                return empty_generator(), []
            return {
                'success': False,
                'answer': msg,
                'sources': []
            }

        chunk_ids = [chunk.get('chunk_id') for chunk in top_chunks]
        reranked_chunks = manager._rerank_chunks(query, chunk_ids)
        top_3_chunk_ids = reranked_chunks[:3]

        manager.logger.info("Top 3 chunks for query '%s': %s", query, top_3_chunk_ids)
        context_chunks = manager._get_context_chunks(top_3_chunk_ids)
        sources = manager._get_sources(context_chunks)
        manager.logger.info("Sources for query '%s': %s", query, sources)

        if stream:
            stream_generator = manager._generate_answer(query, context_chunks, conversation_history, stream=True)

            def save_and_stream():
                collected_answer = []
                try:
                    for chunk in stream_generator:
                        collected_answer.append(chunk)
                        yield chunk
                    full_answer = "".join(collected_answer)
                    manager._save_query(query, full_answer, user_id)
                except Exception as e:
                    manager.logger.info("Error in streaming answer: %s", str(e), exc_info=True)
                    yield "\nError occurred during streaming."

            return save_and_stream(), sources
        else:
            answer = manager._generate_answer(query, context_chunks, conversation_history)
            manager._save_query(query, answer, user_id)
            return {
                'success': True,
                'answer': answer,
                'sources': sources
            }

    except Exception as e:
        manager.logger.info("Error answering query '%s': %s", query, str(e), exc_info=True)
        error_msg = 'Sorry, an error occurred while processing your question.'
        if stream:
            def error_generator():
                yield error_msg
            return error_generator(), []
        return {
            'success': False,
            'error': str(e),
            'answer': error_msg
        }


def get_reranking_model(manager):
    """Get the reranking model from the centralized LLM engine."""
    try:
        from src.utils.llm_engine import LLMEngine
        llm_engine = LLMEngine()
        return llm_engine.get_reranking_model()
    except Exception as e:
        manager.logger.info("Failed to get reranking model from LLMEngine: %s", str(e), exc_info=True)
        return None


def rerank_chunks(manager, query: str, chunk_ids: List[str]) -> List[str]:
    """Rerank chunks using LLM for better relevance."""
    if not chunk_ids:
        return []

    try:
        cursor = manager.conn.cursor()
        chunk_contents = {}
        for chunk_id in chunk_ids:
            cursor.execute('SELECT content FROM knowledge_chunks WHERE id = ?', (chunk_id,))
            row = cursor.fetchone()
            if row:
                chunk_contents[chunk_id] = row[0]

        if not chunk_contents:
            manager.logger.warning("No chunk contents found for reranking, returning original order")
            return chunk_ids[:3]

        reranker = manager._get_reranking_model()
        if not reranker:
            manager.logger.warning("Reranker not available, returning original chunk order")
            return chunk_ids[:3]

        candidate_pairs = []
        chunk_id_list = []
        for chunk_id, content in chunk_contents.items():
            candidate_pairs.append((query, content))
            chunk_id_list.append(chunk_id)

        manager.logger.info("Reranking %d chunks using cross-encoder", len(candidate_pairs))
        rerank_scores = reranker.predict(candidate_pairs)

        ranked_chunks = list(zip(chunk_id_list, rerank_scores))
        ranked_chunks.sort(key=lambda x: x[1], reverse=True)
        manager.logger.info("Reranking complete, top score: %s", ranked_chunks[0][1] if ranked_chunks else 'N/A')

        reranked_ids = [chunk_id for chunk_id, _ in ranked_chunks]
        return reranked_ids

    except Exception as e:
        manager.logger.info("Error during chunk reranking: %s", str(e), exc_info=True)
        return chunk_ids[:3]


def get_context_chunks(manager, chunk_ids: List[str]) -> List[Dict[str, Any]]:
    """Get chunks with context (predecessor and successor chunks)."""
    chunks = []
    cursor = manager.conn.cursor()
    metadata_by_chunk = knowledge_ingest.fetch_chunk_metadata(cursor, chunk_ids)

    for chunk_id in chunk_ids:
        cursor.execute(
            '''
            SELECT c.id, c.document_id, c.chunk_index, c.content, d.original_filename 
            FROM knowledge_chunks c
            JOIN knowledge_documents d ON c.document_id = d.id
            WHERE c.id = ?
            ''',
            (chunk_id,)
        )
        main_chunk = cursor.fetchone()

        if not main_chunk:
            continue

        chunk_id_val, doc_id, chunk_index, content, filename = main_chunk

        cursor.execute(
            'SELECT id, content FROM knowledge_chunks WHERE document_id = ? AND chunk_index = ?',
            (doc_id, chunk_index - 1)
        )
        predecessor = cursor.fetchone()

        cursor.execute(
            'SELECT id, content FROM knowledge_chunks WHERE document_id = ? AND chunk_index = ?',
            (doc_id, chunk_index + 1)
        )
        successor = cursor.fetchone()

        chunk_data = {
            'id': chunk_id_val,
            'document_id': doc_id,
            'chunk_index': chunk_index,
            'content': content,
            'filename': filename,
            'metadata': metadata_by_chunk.get(str(chunk_id_val), {})
        }

        if predecessor:
            chunk_data['predecessor'] = {
                'id': predecessor[0],
                'content': predecessor[1]
            }

        if successor:
            chunk_data['successor'] = {
                'id': successor[0],
                'content': successor[1]
            }

        chunks.append(chunk_data)

    return chunks


def generate_answer(
    manager,
    query: str,
    context_chunks: List[Dict[str, Any]],
    conversation_history: Optional[List[Dict[str, str]]] = None,
    stream: bool = False,
):
    """Generate an answer to the query using LLM and context chunks."""
    document_index = {}
    document_counter = 1

    context = ""

    for chunk in context_chunks:
        doc_filename = chunk['filename']
        if doc_filename not in document_index:
            document_index[doc_filename] = document_counter
            document_counter += 1

        doc_num = document_index[doc_filename]
        context += f"\n\nDocument [{doc_num}]:\n"

        metadata = chunk.get('metadata') or {}
        if metadata:
            context += "Metadata: " + ", ".join(f"{k}={v}" for k, v in metadata.items()) + "\n\n"

        if 'predecessor' in chunk:
            context += chunk['predecessor']['content'] + "\n\n"

        context += chunk['content'] + "\n\n"

        if 'successor' in chunk:
            context += chunk['successor']['content']

    conversation_context = ""
    if conversation_history and not is_browser_llm_latest_message_only():
        conversation_context = "\n\nPrevious conversation context:\n"
        recent_history = conversation_history[-6:]
        for msg in recent_history:
            role = msg.get('role', '')
            content = msg.get('content', '')
            if role == 'user':
                conversation_context += f"User: {content}\n"
            elif role == 'assistant':
                conversation_context += f"Assistant: {content}\n"

    doc_reference_list = ""
    if document_index:
        doc_reference_list = "\n\nDocument References:\n"
        for doc_name, doc_num in sorted(document_index.items(), key=lambda x: x[1]):
            doc_reference_list += f"[{doc_num}] {doc_name}\n"

    system_content = f"""You are a helpful AI assistant that provides accurate answers based on the given context. 
If the answer cannot be found in the context, acknowledge that you don't know instead of making up information.
Provide clear, concise answers and use markdown formatting in your response to improve readability.

IMPORTANT CITATION RULES:
- When referencing information from the context, use numbered citations like [1], [2], etc.
- Do NOT repeat the full document names in your response
- Use citations sparingly - only at the end of sentences or paragraphs where you reference specific information
- The document references will be provided separately at the end, so you don't need to mention document names{conversation_context}"""

    prompt = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": f"Context information:\n{context}{doc_reference_list}\n\nQuestion: {query}\n\nProvide a detailed answer to the question based only on the context provided. Use markdown formatting for better readability and numbered citations [1], [2], etc. when referencing specific information."}
    ]

    try:
        return manager.llm_engine.generate_completion(prompt, log_prefix="Knowledge QA", stream=stream)
    except Exception as e:
        manager.logger.info("Error generating answer: %s", str(e), exc_info=True)
        if stream:
            def error_generator():
                yield "I'm sorry, I couldn't generate an answer based on the available information."
            return error_generator()
        return "I'm sorry, I couldn't generate an answer based on the available information."


def get_sources(manager, context_chunks: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Get source information for citation with numbered references."""
    sources = []
    seen_documents = set()
    document_counter = 1

    for chunk in context_chunks:
        doc_filename = chunk['filename']
        if doc_filename not in seen_documents:
            sources.append({
                'document': doc_filename,
                'document_number': document_counter,
                'chunk_id': chunk['id'],
                'metadata': chunk.get('metadata') or {}
            })
            seen_documents.add(doc_filename)
            document_counter += 1

    return sources


def get_document_markdown(manager, document_id: str) -> Optional[str]:
    """Get the markdown content of a processed document."""
    try:
        document_info = manager.get_document_info(document_id)
        if not document_info:
            return None

        if document_info['status'] != 'completed':
            return None

        file_path = document_info['file_path']
        content_type = document_info['content_type']

        if not os.path.exists(file_path):
            return None

        if content_type == 'txt':
            with open(file_path, 'r', encoding='utf-8') as f:
                return f.read()

        try:
            result = manager.md_converter.convert(file_path)
            return result.text_content
        except Exception as e:
            manager.logger.error("Error converting document to markdown: %s", str(e), exc_info=True)
            return None

    except Exception as e:
        manager.logger.error("Error getting document markdown %s: %s", document_id, str(e), exc_info=True)
        return None
