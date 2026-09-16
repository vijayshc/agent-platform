"""
Skill Vectorizer for MCP Skill Library Server.
Handles embedding skills into vector store for similarity search.
"""

import logging
import time
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime

from src.utils.vector_store import VectorStore
from src.utils.llm_engine import LLMEngine
from src.models.skill import Skill, SkillStatus


class SkillVectorizer:
    """Handles embedding skills into vector store for similarity search"""
    
    def __init__(self):
        """Initialize the skill vectorizer"""
        self.logger = logging.getLogger('text2sql.skill_vectorizer')
        self.logger.info("Initializing Skill Vectorizer")
        
        # Initialize vector store
        self.vector_store = VectorStore()
        self.vector_store.connect()
        # Create a collection for skills
        self.vector_store.init_collection('skills')
        
        # Initialize LLM engine for embeddings
        self.llm_engine = LLMEngine()
        
        # Track processed skills
        self.processed_count = 0
    
    def process_all_skills(self) -> bool:
        """Process and embed all active skills
        
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            self.logger.info("Starting skill vectorization process")
            start_time = time.time()
            
            # Get all active skills
            skills = Skill.get_all(status=SkillStatus.ACTIVE.value)
            
            # Clear existing skills from vector store
            self.vector_store.clear_collection('skills')
            
            processed_skills = 0
            
            # Process each skill
            for skill in skills:
                if self._embed_and_store_skill(skill):
                    processed_skills += 1
                else:
                    self.logger.warning(f"Failed to process skill: {skill.name}")
            
            elapsed_time = time.time() - start_time
            self.logger.info(f"Skill vectorization completed: {processed_skills}/{len(skills)} skills processed in {elapsed_time:.2f} seconds")
            
            return processed_skills == len(skills)
            
        except Exception as e:
            self.logger.error(f"Error processing skills: {str(e)}", exc_info=True)
            return False
    
    def add_skill(self, skill: Skill) -> bool:
        """Add a single skill to the vector store
        
        Args:
            skill: Skill object to add
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            return self._embed_and_store_skill(skill)
        except Exception as e:
            self.logger.error(f"Error adding skill {skill.name}: {str(e)}", exc_info=True)
            return False
    
    def update_skill(self, skill: Skill) -> bool:
        """Update a skill in the vector store
        
        Args:
            skill: Updated skill object
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            # Remove existing entry if it exists
            self.remove_skill(skill.skill_id)
            
            # Add updated skill
            return self._embed_and_store_skill(skill)
        except Exception as e:
            self.logger.error(f"Error updating skill {skill.name}: {str(e)}", exc_info=True)
            return False
    
    def remove_skill(self, skill_id: str) -> bool:
        """Remove a skill from the vector store
        
        Args:
            skill_id: Skill ID to remove
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            # Remove from vector store using filter
            filter_expr = {"skill_id": skill_id}
            result = self.vector_store.delete_by_filter('skills', filter_expr)
            
            if result:
                self.logger.info(f"Removed skill {skill_id} from vector store")
            else:
                self.logger.warning(f"Failed to remove skill {skill_id} from vector store")
                
            return result
        except Exception as e:
            self.logger.error(f"Error removing skill {skill_id}: {str(e)}", exc_info=True)
            return False
    
    def _embed_and_store_skill(self, skill: Skill) -> bool:
        """Embed skill and store in vector database
        
        Args:
            skill: Skill object to embed
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            # Get searchable text for the skill
            skill_text = skill.get_searchable_text()
            
            # Get embedding for skill text
            embedding = self._get_embedding(skill_text)
            
            # Create identifier for this skill
            vector_id = self.processed_count
            self.processed_count += 1
            
            # Store skill in vector database
            metadata = {
                "skill_id": skill.skill_id,
                "name": skill.name,
                "category": skill.category,
                "description": skill.description,
                "tags": skill.tags,
                "version": skill.version,
                "text": skill_text
            }
            
            result = self.vector_store.insert_embedding(
                'skills',
                vector_id,
                embedding,
                skill_text,
                metadata
            )
            
            if result:
                self.logger.debug(f"Successfully embedded skill: {skill.name}")
            else:
                self.logger.warning(f"Failed to embed skill: {skill.name}")
            
            return result
            
        except Exception as e:
            self.logger.error(f"Error embedding skill {skill.name}: {str(e)}", exc_info=True)
            return False
    
    def _get_embedding(self, text: str) -> List[float]:
        """Get embedding for text using the centralized LLM engine
        
        Args:
            text: Text to embed
            
        Returns:
            List[float]: Vector embedding
        """
        # Use the centralized LLM engine to generate embeddings
        embedding = self.llm_engine.generate_embedding(text)
        
        # Convert to list if it's a numpy array
        if hasattr(embedding, 'tolist'):
            return embedding.tolist()
        return embedding
    
    def search_skills(self, query: str, limit: int = 10, 
                     category_filter: Optional[str] = None) -> List[Dict[str, Any]]:
        """Search for skills matching the query with optional category filtering
        
        Args:
            query: Search query
            limit: Maximum number of results to return
            category_filter: Optional category to filter results by (from existing skill categories)
            
        Returns:
            List[Dict]: List of matching skills with similarity scores
        """
        try:
            # Get embedding for query
            query_embedding = self._get_embedding(query)
            
            # Create filter expression if category is specified
            filter_expr = None
            if category_filter:
                filter_expr = {"category": category_filter}
            
            # Search for similar vectors
            results = self.vector_store.search_similar(
                'skills',
                query_embedding,
                limit=limit,
                filter_expr=filter_expr,
                output_fields=["id", "text", "metadata"]
            )
            
            # Enhance results with skill details
            enhanced_results = []
            for result in results:
                metadata = result.get('metadata', {})
                enhanced_result = {
                    'skill_id': metadata.get('skill_id'),
                    'name': metadata.get('name'),
                    'category': metadata.get('category'),
                    'description': metadata.get('description'),
                    'tags': metadata.get('tags', []),
                    'version': metadata.get('version'),
                    'similarity_score': result.get('distance', 0.0),
                    'text': metadata.get('text', '')
                }
                enhanced_results.append(enhanced_result)
            
            return enhanced_results
            
        except Exception as e:
            self.logger.error(f"Error searching skills: {str(e)}", exc_info=True)
            return []

    def search_skills_vector(self, query: str, limit: int = 50) -> Tuple[List[Dict[str, Any]], str]:
        """Perform pure vector search for skills without any filtering
        
        Args:
            query: User query
            limit: Maximum number of results
            
        Returns:
            Tuple[List[Dict], str]: Search results and search description
        """
        try:
            self.logger.info(f"Performing vector search for skills: {query}")
            
            # Perform vector search without any filters
            results = self.vector_store.search_similar(
                'skills',
                self._get_embedding(query),
                limit=limit,
                filter_expr=None,
                output_fields=["id", "text", "metadata"]
            )
            
            # Enhance results with skill details
            enhanced_results = []
            for result in results:
                metadata = result.get('metadata', {})
                enhanced_result = {
                    'skill_id': metadata.get('skill_id'),
                    'name': metadata.get('name'),
                    'category': metadata.get('category'),
                    'description': metadata.get('description'),
                    'tags': metadata.get('tags', []),
                    'version': metadata.get('version'),
                    'similarity_score': result.get('distance', 0.0),
                    'text': metadata.get('text', '')
                }
                enhanced_results.append(enhanced_result)
            
            search_description = f"Vector search returned {len(enhanced_results)} results"
            return enhanced_results, search_description
            
        except Exception as e:
            self.logger.error(f"Error in skill vector search: {str(e)}", exc_info=True)
            return [], "Search failed"

    def get_stats(self) -> Dict[str, Any]:
        """Get statistics about skills in the vector database
        
        Returns:
            Dict: Statistics including total skills, categories, etc.
        """
        try:
            # Get total count of vectors in the skills collection
            count = self.vector_store.count('skills')
            if count == 0:
                return None
            
            # Get category distribution from database
            categories = Skill.get_categories_with_counts()
            
            # Build stats dictionary
            stats = {
                'total_skills': count,
                'total_categories': len(categories),
                'category_distribution': categories,
                'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M')
            }
            
            return stats
            
        except Exception as e:
            self.logger.error(f"Error getting skill stats: {str(e)}", exc_info=True)
            return None
