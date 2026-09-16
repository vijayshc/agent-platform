from flask import Blueprint, request, jsonify, session, current_app, send_from_directory
import logging
import os
import tempfile
import uuid
from datetime import datetime
from werkzeug.utils import secure_filename
from src.utils.knowledge_manager import KnowledgeManager
from src.utils import chunking, collection_access, knowledge_access, knowledge_ingest, tabular_ingest
from src.auth.decorators import module_required, current_user_id_for_rbac
from src.utils.auth_utils import login_required
from src.utils.user_manager import UserManager
from config.config import CHUNK_SIZE, CHUNK_OVERLAP

# Blueprint for knowledge base routes
knowledge_bp = Blueprint('knowledge', __name__)
logger = logging.getLogger('text2sql.knowledge_routes')

# Initialize knowledge manager with lazy loading
knowledge_manager = None
# Initialize user manager for audit logging
user_manager = UserManager()

def get_knowledge_manager():
    """Get knowledge manager instance with lazy initialization"""
    global knowledge_manager
    if knowledge_manager is None:
        knowledge_manager = KnowledgeManager()
    return knowledge_manager


def _requester_id():
    """Authenticated user id for session, JWT or API-key callers."""
    return current_user_id_for_rbac()


def _form_value(form, key):
    """Read one logical value from a form, joining repeated fields into a list."""
    values = form.getlist(key)
    if not values:
        return None
    return values[0] if len(values) == 1 else values


def _ingest_options_from(payload):
    """Normalise upload/text ingestion options, raising ValueError for bad input."""
    return knowledge_ingest.normalize_options({
        'chunking_method': payload.get('chunking_method'),
        'chunk_size': payload.get('chunk_size'),
        'chunk_overlap': payload.get('chunk_overlap'),
        'metadata_columns': _form_value(payload, 'metadata_columns') if hasattr(payload, 'getlist') else payload.get('metadata_columns'),
        'data_columns': _form_value(payload, 'data_columns') if hasattr(payload, 'getlist') else payload.get('data_columns'),
        'collection_name': payload.get('collection_name'),
    })


def _authorize_collection(options):
    """Return ``(options, None)`` or ``(None, error_response)`` for the target collection.

    Indexing into a collection requires an explicit role grant (or admin);
    knowledge uploaders can select a collection but never create one.
    """
    name = options.collection_name
    if not collection_access.can_access_collection(name, _requester_id()):
        return None, (
            jsonify({
                'success': False,
                'error': f'You are not authorized to use the collection "{name}". Ask an administrator to grant your role access.',
            }),
            403,
        )
    try:
        manager = get_knowledge_manager()
        names = manager.vector_store.list_collections() if manager.vector_store else []
    except Exception:
        names = []
    if name not in names:
        return None, (
            jsonify({'success': False, 'error': f'Collection "{name}" does not exist.'}),
            400,
        )
    return options, None


def _document_or_error(document_id, *, manage=False):
    """Return ``(document_info, None)`` or ``(None, error_response)``.

    404 when the document does not exist; 403 when it exists but the caller may
    neither view it (``manage=False``) nor modify it (``manage=True``).  A role
    grant confers view/use only; delete and tag mutations require owner or admin.
    """
    info = get_knowledge_manager().get_document_info(document_id)
    if not info:
        return None, (jsonify({'success': False, 'error': 'Document not found'}), 404)

    user_id = _requester_id()
    if manage:
        allowed = knowledge_access.can_manage_document(document_id, user_id)
    else:
        allowed = knowledge_access.can_access_document(document_id, user_id)

    if not allowed:
        return None, (
            jsonify({
                'success': False,
                'error': 'You are not authorized to access this document',
            }),
            403,
        )
    return info, None


# Admin route for document management
@knowledge_bp.route('/admin/knowledge')
@login_required
@module_required("knowledge")
def admin_knowledge():
    """Render the React admin interface for knowledge base management"""
    from src.routes.agent_routes import _render_admin_app
    return _render_admin_app()


@knowledge_bp.route('/api/knowledge/documents', methods=['GET'])
@login_required
@module_required("knowledge")
def list_knowledge_documents():
    """JSON list of the documents the caller may see (server-side filtered)."""
    try:
        documents = get_knowledge_manager().list_documents(user_id=_requester_id())
        return jsonify({"success": True, "documents": documents})
    except Exception as e:
        logger.exception("Error listing knowledge documents")
        return jsonify({"success": False, "error": str(e)}), 500

# Route to handle document upload
@knowledge_bp.route('/api/knowledge/upload', methods=['POST'])
@login_required
@module_required("knowledge")
def upload_document():
    """Handle document upload and processing"""
    if 'document' not in request.files:
        return jsonify({'success': False, 'error': 'No document part'}), 400
        
    file = request.files['document']
    if file.filename == '':
        return jsonify({'success': False, 'error': 'No selected document'}), 400
        
    if file:
        # Validate the ingestion properties before persisting anything so an
        # invalid method/size is a clean 400 rather than an orphaned upload.
        try:
            options = _ingest_options_from(request.form)
        except ValueError as exc:
            return jsonify({'success': False, 'error': str(exc)}), 400
        options, error = _authorize_collection(options)
        if error is not None:
            return error

        # Generate a unique filename
        original_filename = secure_filename(file.filename)
        filename = f"{uuid.uuid4()}_{original_filename}"
        
        # Save the file
        file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
        file.save(file_path)
        
        # Get tags from the form data (comma-separated string)
        tags = []
        if 'tags' in request.form:
            tags_string = request.form.get('tags', '')
            if tags_string:
                tags = [tag.strip() for tag in tags_string.split(',') if tag.strip()]
        
        # Get allowed roles from the form data
        allowed_roles = []
        if 'allowed_roles' in request.form:
            # Check if it's a list (multiple select) or a string (comma-separated)
            roles_list = request.form.getlist('allowed_roles')
            if len(roles_list) > 1:
                allowed_roles = roles_list
            elif len(roles_list) == 1:
                # Could be a single role or a comma-separated string
                val = roles_list[0]
                if ',' in val:
                    allowed_roles = [role.strip() for role in val.split(',') if role.strip()]
                else:
                    allowed_roles = [val]
        
        # Process the document asynchronously; the uploader is stamped as owner.
        document_id = get_knowledge_manager().process_document(
            file_path, original_filename, tags, allowed_roles,
            owner_id=_requester_id(), options=options
        )
        
        return jsonify({
            'success': True, 
            'message': 'Document uploaded and processing started',
            'documentId': document_id,
            'accessId': knowledge_access.access_id_for(document_id),
            'originalFilename': original_filename,
            'tags': tags,
            'allowed_roles': allowed_roles,
            'chunking_method': options.chunking_method,
            'chunk_size': options.chunk_size,
            'chunk_overlap': options.chunk_overlap,
            'metadata_columns': options.metadata_columns,
            'data_columns': options.data_columns,
            'collection_name': options.collection_name
        })
    
    return jsonify({'success': False, 'error': 'Failed to upload document'}), 400


@knowledge_bp.route('/api/knowledge/inspect', methods=['POST'])
@login_required
@module_required("knowledge")
def inspect_document():
    """Describe a CSV/Excel upload's columns and preview before it is indexed.

    The file is parsed from a temporary copy and never persisted, so the UI can
    let the uploader map data/metadata columns without creating a document.
    """
    if 'document' not in request.files:
        return jsonify({'success': False, 'error': 'No document part'}), 400
    file = request.files['document']
    if not file or file.filename == '':
        return jsonify({'success': False, 'error': 'No selected document'}), 400

    original_filename = secure_filename(file.filename)
    _, ext = os.path.splitext(original_filename)
    content_type = ext.lower().strip('.')
    if not tabular_ingest.is_tabular(content_type):
        return jsonify({'success': True, 'kind': 'document', 'content_type': content_type})

    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=f".{content_type}") as temp_file:
            file.save(temp_file)
            temp_path = temp_file.name
        info = tabular_ingest.inspect_table(temp_path, content_type)
        return jsonify({'success': True, 'kind': 'tabular', 'content_type': content_type, **info})
    except Exception as exc:
        current_app.logger.error(f"Error inspecting document {original_filename}: {exc}", exc_info=True)
        return jsonify({'success': False, 'error': f'Could not read file: {exc}'}), 400
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)

# Route to handle direct text input
@knowledge_bp.route('/api/knowledge/text', methods=['POST'])
@login_required
@module_required("knowledge")
def add_text_content():
    """Handle direct text input and processing"""
    data = request.get_json()
    
    if not data:
        return jsonify({'success': False, 'error': 'No data provided'}), 400
        
    # Extract required fields
    name = data.get('name')
    content_type = data.get('content_type')
    content = data.get('content')
    tags = data.get('tags', [])
    allowed_roles = data.get('allowed_roles', [])
    
    # Validate required fields
    if not name or not content_type or not content:
        return jsonify({'success': False, 'error': 'Missing required fields'}), 400

    try:
        options = _ingest_options_from(data)
    except ValueError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 400
    options, error = _authorize_collection(options)
    if error is not None:
        return error
    
    try:
        # Process the text content; the submitter is stamped as owner.
        document_id = get_knowledge_manager().process_text_content(
            name, content_type, content, tags, allowed_roles,
            owner_id=_requester_id(), options=options
        )
        
        return jsonify({
            'success': True, 
            'message': 'Text content submitted for processing',
            'documentId': document_id,
            'accessId': knowledge_access.access_id_for(document_id),
            'name': name,
            'tags': tags,
            'allowed_roles': allowed_roles,
            'chunking_method': options.chunking_method,
            'chunk_size': options.chunk_size,
            'chunk_overlap': options.chunk_overlap,
            'collection_name': options.collection_name
        })
    except Exception as e:
        current_app.logger.error(f"Error processing text content: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': f'Failed to process text content: {str(e)}'}), 500

# Route to check document processing status
@knowledge_bp.route('/api/knowledge/status/<document_id>', methods=['GET'])
@login_required
@module_required("knowledge")
def document_status(document_id):
    """Get the processing status of a document the caller may view"""
    _, error = _document_or_error(document_id)
    if error is not None:
        return error
    status = get_knowledge_manager().get_document_status(document_id)
    return jsonify(status)

# Route to delete a document
@knowledge_bp.route('/api/knowledge/delete/<document_id>', methods=['DELETE'])
@login_required
@module_required("knowledge")
def delete_document(document_id):
    """Delete a document and its chunks from the system (owner or admin only)"""
    _, error = _document_or_error(document_id, manage=True)
    if error is not None:
        return error
    success = get_knowledge_manager().delete_document(document_id)
    if success:
        return jsonify({'success': True, 'message': 'Document deleted successfully'})
    else:
        return jsonify({'success': False, 'error': 'Failed to delete document'}), 500

# Route to get all available tags
@knowledge_bp.route('/api/knowledge/tags', methods=['GET'])
@login_required
@module_required("knowledge")
def get_all_tags():
    """Tags from active documents the caller may see (server-side filtered)"""
    tags = get_knowledge_manager().get_all_tags(user_id=_requester_id())
    return jsonify({'success': True, 'tags': tags})

# Route to add a tag to a document
@knowledge_bp.route('/api/knowledge/tag/add', methods=['POST'])
@login_required
@module_required("knowledge")
def add_document_tag():
    """Add a tag to a document (owner or admin only)"""
    data = request.get_json()
    
    if not data or 'document_id' not in data or 'tag' not in data:
        return jsonify({'success': False, 'error': 'Missing document_id or tag'}), 400
        
    document_id = data.get('document_id')
    tag = data.get('tag')
    
    _, error = _document_or_error(document_id, manage=True)
    if error is not None:
        return error

    success = get_knowledge_manager().add_document_tag(document_id, tag)
    
    if success:
        return jsonify({'success': True, 'message': 'Tag added successfully'})
    else:
        return jsonify({'success': False, 'error': 'Failed to add tag'}), 500

# Route to remove a tag from a document
@knowledge_bp.route('/api/knowledge/tag/remove', methods=['POST'])
@login_required
@module_required("knowledge")
def remove_document_tag():
    """Remove a tag from a document (owner or admin only)"""
    data = request.get_json()
    
    if not data or 'document_id' not in data or 'tag' not in data:
        return jsonify({'success': False, 'error': 'Missing document_id or tag'}), 400
        
    document_id = data.get('document_id')
    tag = data.get('tag')
    
    _, error = _document_or_error(document_id, manage=True)
    if error is not None:
        return error

    success = get_knowledge_manager().remove_document_tag(document_id, tag)
    
    if success:
        return jsonify({'success': True, 'message': 'Tag removed successfully'})
    else:
        return jsonify({'success': False, 'error': 'Failed to remove tag'}), 500

# Route to view original uploaded document
@knowledge_bp.route('/api/knowledge/view/original/<document_id>')
@login_required
@module_required("knowledge")
def view_original_document(document_id):
    """View the original uploaded document"""
    try:
        document_info, error = _document_or_error(document_id)
        if error is not None:
            return error
        
        file_path = document_info['file_path']
        original_filename = document_info['original_filename']
        content_type = document_info['content_type']
        
        # Check if file exists
        if not os.path.exists(file_path):
            return jsonify({'success': False, 'error': 'File not found on disk'}), 404
        
        # Determine the MIME type for the response
        mime_type = 'application/octet-stream'  # Default
        if content_type:
            content_type_lower = content_type.lower()
            if content_type_lower == 'pdf':
                mime_type = 'application/pdf'
            elif content_type_lower in ['doc', 'docx']:
                mime_type = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
            elif content_type_lower in ['xls', 'xlsx']:
                mime_type = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            elif content_type_lower in ['ppt', 'pptx']:
                mime_type = 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
            elif content_type_lower == 'txt':
                mime_type = 'text/plain'
        
        return send_from_directory(
            os.path.dirname(file_path),
            os.path.basename(file_path),
            as_attachment=False,
            download_name=original_filename,
            mimetype=mime_type
        )
    except Exception as e:
        current_app.logger.error(f"Error viewing original document {document_id}: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': 'Failed to load document'}), 500

# Route to view markdown output of document
@knowledge_bp.route('/api/knowledge/view/markdown/<document_id>')
@login_required
@module_required("knowledge")
def view_markdown_document(document_id):
    """View the markdown output of the processed document"""
    try:
        document_info, error = _document_or_error(document_id)
        if error is not None:
            return error

        markdown_content = get_knowledge_manager().get_document_markdown(document_id)
        if not markdown_content:
            return jsonify({'success': False, 'error': 'Document not found or not processed'}), 404
        
        original_filename = document_info['original_filename'] if document_info else 'Unknown'
        
        return jsonify({
            'success': True,
            'markdown_content': markdown_content,
            'original_filename': original_filename
        })
    except Exception as e:
        current_app.logger.error(f"Error viewing markdown document {document_id}: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': 'Failed to load markdown content'}), 500

# Route to get document information
@knowledge_bp.route('/api/knowledge/info/<document_id>')
@login_required
@module_required("knowledge")
def get_document_info(document_id):
    """Get detailed information about a document the caller may view"""
    try:
        document_info, error = _document_or_error(document_id)
        if error is not None:
            return error
        
        return jsonify({
            'success': True,
            'document': document_info
        })
    except Exception as e:
        current_app.logger.error(f"Error getting document info {document_id}: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': 'Failed to get document information'}), 500

# Route to get all available roles
@knowledge_bp.route('/api/knowledge/roles', methods=['GET'])
@login_required
@module_required("knowledge")
def get_all_roles():
    """Get a list of all available roles in the system"""
    roles = user_manager.get_all_roles()
    role_names = [role.name for role in roles]
    return jsonify({'success': True, 'roles': role_names})


@knowledge_bp.route('/api/knowledge/ingest-options', methods=['GET'])
@login_required
@module_required("knowledge")
def get_ingest_options():
    """Chunking methods, defaults and tabular types the upload UI should offer."""
    return jsonify({
        'success': True,
        'methods': [
            {'id': key, 'label': meta['label'], 'description': meta['description']}
            for key, meta in chunking.METHODS.items()
        ],
        'defaults': {
            'chunking_method': chunking.DEFAULT_METHOD,
            'chunk_size': CHUNK_SIZE,
            'chunk_overlap': CHUNK_OVERLAP,
        },
        'tabular_types': sorted(tabular_ingest.TABULAR_CONTENT_TYPES),
    })


@knowledge_bp.route('/api/knowledge/collections', methods=['GET'])
@login_required
@module_required("knowledge")
def list_knowledge_collections():
    """Vector collections the caller may index into (role-grant filtered)."""
    try:
        manager = get_knowledge_manager()
        names = manager.vector_store.list_collections() if manager.vector_store else []
    except Exception as exc:
        current_app.logger.error(f"Error listing vector collections: {exc}", exc_info=True)
        return jsonify({'success': False, 'error': 'Could not read the vector database collections'}), 503

    visible = collection_access.visible_collection_names(names, _requester_id())
    return jsonify({
        'success': True,
        'collections': [
            {
                'name': name,
                'access_id': collection_access.access_id_for(name),
                'roles': collection_access.granted_role_names(name),
            }
            for name in visible
        ],
    })
