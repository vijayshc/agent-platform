"""
Skill management routes for admin interface
"""

from collections import Counter

from flask import Blueprint, request, jsonify, render_template
from src.models.skill import (
    Skill,
    SkillCategory,
    SkillStatus,
    can_access_skill_id,
    can_manage_skill_id,
    visible_skill_ids,
    visible_skill_uuids,
)
from src.auth.decorators import current_user_id_for_rbac, module_required
from src.auth.resource_access import is_admin
from src.utils.skill_vectorizer import SkillVectorizer
import logging
import json

skill_bp = Blueprint('skill', __name__)
logger = logging.getLogger('text2sql.skill_routes')

# Initialize skill vectorizer (lazy loading)
_skill_vectorizer = None

def get_skill_vectorizer():
    """Get skill vectorizer instance (lazy loading)"""
    global _skill_vectorizer
    if _skill_vectorizer is None:
        _skill_vectorizer = SkillVectorizer()
    return _skill_vectorizer


def _forbidden(message: str = "You do not have access to this skill"):
    return jsonify({'success': False, 'error': message}), 403


def _visible_or_all(user_id, skills):
    """Filter a skill list to what ``user_id`` may see (``None`` = admin)."""
    visible = visible_skill_ids(user_id)
    if visible is None:
        return skills
    return [skill for skill in skills if skill.id in visible]


def _category_counts(skills) -> list[dict]:
    counts = Counter(skill.category for skill in skills)
    return [
        {'category': category, 'count': count}
        for category, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


@skill_bp.route('/skills')
@skill_bp.route('/admin/skills')
@module_required("skills")
def skills_page():
    """Skills management page (React)"""
    from src.routes.agent_routes import _render_admin_app
    return _render_admin_app()

@skill_bp.route('/api/skills', methods=['GET'])
@module_required("skills")
def list_skills():
    """List all skills with optional filtering"""
    try:
        category = request.args.get('category')
        status = request.args.get('status', SkillStatus.ACTIVE.value)
        user_id = current_user_id_for_rbac()
        
        skills = _visible_or_all(user_id, Skill.get_all(category=category, status=status))
        
        # Convert to dict for JSON serialization
        skills_data = [skill.to_dict() for skill in skills]
        
        return jsonify({
            'success': True,
            'skills': skills_data,
            'total': len(skills_data)
        })
        
    except Exception as e:
        logger.error(f"Error listing skills: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@skill_bp.route('/api/skills/<skill_id>', methods=['GET'])
@module_required("skills")
def get_skill(skill_id):
    """Get a specific skill by ID"""
    try:
        user_id = current_user_id_for_rbac()
        skill = Skill.get_by_id(skill_id)
        
        if not skill:
            return jsonify({
                'success': False,
                'error': 'Skill not found'
            }), 404
        
        if not can_access_skill_id(skill.id, user_id):
            return _forbidden()
        
        return jsonify({
            'success': True,
            'skill': skill.to_dict()
        })
        
    except Exception as e:
        logger.error(f"Error getting skill {skill_id}: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@skill_bp.route('/api/skills', methods=['POST'])
@module_required("skills")
def create_skill():
    """Create a new skill"""
    try:
        data = request.get_json()
        user_id = current_user_id_for_rbac()
        
        # Validate required fields
        required_fields = ['name', 'description', 'category', 'steps']
        for field in required_fields:
            if not data.get(field):
                return jsonify({
                    'success': False,
                    'error': f'Missing required field: {field}'
                }), 400
        
        # Validate category
        if data['category'] not in [cat.value for cat in SkillCategory]:
            return jsonify({
                'success': False,
                'error': f'Invalid category: {data["category"]}'
            }), 400
        
        # Create skill owned by the authenticated user
        skill = Skill(
            name=data['name'],
            description=data['description'],
            category=data['category'],
            tags=data.get('tags', []),
            prerequisites=data.get('prerequisites', []),
            steps=data['steps'],
            examples=data.get('examples', []),
            status=data.get('status', SkillStatus.ACTIVE.value),
            version=data.get('version', '1.0'),
            created_by=data.get('created_by', 'admin'),
            owner_id=user_id,
        )
        
        # Save to database
        skill.save()
        
        # Add to vector store if active
        if skill.status == SkillStatus.ACTIVE.value:
            vectorizer = get_skill_vectorizer()
            vectorizer.add_skill(skill)
        
        logger.info(f"Created skill: {skill.name} (ID: {skill.skill_id})")
        
        return jsonify({
            'success': True,
            'skill': skill.to_dict(),
            'message': 'Skill created successfully'
        })
        
    except Exception as e:
        logger.error(f"Error creating skill: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@skill_bp.route('/api/skills/<skill_id>', methods=['PUT'])
@module_required("skills")
def update_skill(skill_id):
    """Update an existing skill"""
    try:
        user_id = current_user_id_for_rbac()
        skill = Skill.get_by_id(skill_id)
        
        if not skill:
            return jsonify({
                'success': False,
                'error': 'Skill not found'
            }), 404
        
        # Editing (including rename) is reserved for the owner and admins.
        if not can_manage_skill_id(skill.id, user_id):
            return _forbidden("Only the owner or an administrator may modify this skill")
        
        data = request.get_json()
        
        # Update fields if provided
        if 'name' in data:
            skill.name = data['name']
        if 'description' in data:
            skill.description = data['description']
        if 'category' in data:
            if data['category'] not in [cat.value for cat in SkillCategory]:
                return jsonify({
                    'success': False,
                    'error': f'Invalid category: {data["category"]}'
                }), 400
            skill.category = data['category']
        if 'tags' in data:
            skill.tags = data['tags']
        if 'prerequisites' in data:
            skill.prerequisites = data['prerequisites']
        if 'steps' in data:
            skill.steps = data['steps']
        if 'examples' in data:
            skill.examples = data['examples']
        if 'status' in data:
            skill.status = data['status']
        if 'version' in data:
            skill.version = data['version']
        
        # Save to database
        skill.save()
        
        # Update vector store
        vectorizer = get_skill_vectorizer()
        if skill.status == SkillStatus.ACTIVE.value:
            vectorizer.update_skill(skill)
        else:
            # Remove from vector store if not active
            vectorizer.remove_skill(skill.skill_id)
        
        logger.info(f"Updated skill: {skill.name} (ID: {skill.skill_id})")
        
        return jsonify({
            'success': True,
            'skill': skill.to_dict(),
            'message': 'Skill updated successfully'
        })
        
    except Exception as e:
        logger.error(f"Error updating skill {skill_id}: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@skill_bp.route('/api/skills/<skill_id>', methods=['DELETE'])
@module_required("skills")
def delete_skill(skill_id):
    """Delete a skill"""
    try:
        user_id = current_user_id_for_rbac()
        skill = Skill.get_by_id(skill_id)
        
        if not skill:
            return jsonify({
                'success': False,
                'error': 'Skill not found'
            }), 404
        
        # Deletion is reserved for the owner and admins.
        if not can_manage_skill_id(skill.id, user_id):
            return _forbidden("Only the owner or an administrator may delete this skill")
        
        skill_name = skill.name
        
        # Remove from vector store
        vectorizer = get_skill_vectorizer()
        vectorizer.remove_skill(skill.skill_id)
        
        # Delete from database
        skill.delete()
        
        logger.info(f"Deleted skill: {skill_name} (ID: {skill_id})")
        
        return jsonify({
            'success': True,
            'message': 'Skill deleted successfully'
        })
        
    except Exception as e:
        logger.error(f"Error deleting skill {skill_id}: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@skill_bp.route('/api/skills/categories', methods=['GET'])
@module_required("skills")
def list_categories():
    """List all skill categories with counts the caller may see"""
    try:
        user_id = current_user_id_for_rbac()
        skills = _visible_or_all(user_id, Skill.get_all(status=SkillStatus.ACTIVE.value))
        category_counts = {row['category']: row['count'] for row in _category_counts(skills)}
        
        # Add all available categories (including empty ones)
        all_categories = []
        for category in SkillCategory:
            all_categories.append({
                'value': category.value,
                'label': category.value.replace('_', ' ').title(),
                'count': category_counts.get(category.value, 0)
            })
        
        return jsonify({
            'success': True,
            'categories': all_categories
        })
        
    except Exception as e:
        logger.error(f"Error listing categories: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@skill_bp.route('/api/skills/search', methods=['POST'])
@module_required("skills", read_methods=("GET", "HEAD", "OPTIONS", "POST"))
def search_skills():
    """Search skills using vector search"""
    try:
        data = request.get_json()
        query = data.get('query', '')
        category = data.get('category')
        limit = data.get('limit', 10)
        
        if not query:
            return jsonify({
                'success': False,
                'error': 'Query is required'
            }), 400
        
        vectorizer = get_skill_vectorizer()
        
        # Use vector search for skills
        results, search_description = vectorizer.search_skills_vector(query, limit)
        
        # Results are global vectors; drop hits the caller cannot access.
        visible = visible_skill_uuids(current_user_id_for_rbac())
        if visible is not None:
            results = [row for row in results if row.get('skill_id') in visible]
        
        return jsonify({
            'success': True,
            'results': results,
            'search_method': search_description,
            'total': len(results)
        })
        
    except Exception as e:
        logger.error(f"Error searching skills: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@skill_bp.route('/api/skills/vectorize', methods=['POST'])
@module_required("skills")
def vectorize_skills():
    """Reprocess all skills into vector store (administrator only)"""
    try:
        if not is_admin(current_user_id_for_rbac()):
            return _forbidden("Only an administrator may reindex the skill vector store")
        vectorizer = get_skill_vectorizer()
        success = vectorizer.process_all_skills()
        
        if success:
            return jsonify({
                'success': True,
                'message': 'Skills vectorization completed successfully'
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Skills vectorization failed'
            }), 500
        
    except Exception as e:
        logger.error(f"Error vectorizing skills: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@skill_bp.route('/api/skills/stats', methods=['GET'])
@module_required("skills")
def get_skill_stats():
    """Get skill library statistics for the skills the caller may see"""
    try:
        user_id = current_user_id_for_rbac()
        if not is_admin(user_id):
            # Never leak a global count to a tenant: report their own library.
            skills = _visible_or_all(user_id, Skill.get_all(status=SkillStatus.ACTIVE.value))
            counts = _category_counts(skills)
            from datetime import datetime
            return jsonify({
                'success': True,
                'stats': {
                    'total_skills': len(skills),
                    'total_categories': len(counts),
                    'category_distribution': counts,
                    'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M'),
                }
            })
        
        vectorizer = get_skill_vectorizer()
        stats = vectorizer.get_stats()
        
        if stats:
            return jsonify({
                'success': True,
                'stats': stats
            })
        else:
            return jsonify({
                'success': True,
                'stats': {
                    'total_skills': 0,
                    'message': 'No skills found in vector store'
                }
            })
        
    except Exception as e:
        logger.error(f"Error getting skill stats: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@skill_bp.route('/api/skills/import', methods=['POST'])
@module_required("skills")
def import_skills():
    """Import skills from JSON data"""
    try:
        data = request.get_json()
        skills_data = data.get('skills', [])
        user_id = current_user_id_for_rbac()
        
        if not skills_data:
            return jsonify({
                'success': False,
                'error': 'No skills data provided'
            }), 400
        
        imported_count = 0
        errors = []
        
        vectorizer = get_skill_vectorizer()
        
        for skill_data in skills_data:
            try:
                # Validate required fields
                required_fields = ['name', 'description', 'category', 'steps']
                for field in required_fields:
                    if not skill_data.get(field):
                        raise ValueError(f'Missing required field: {field}')
                
                # Create skill owned by the authenticated user
                skill = Skill(
                    name=skill_data['name'],
                    description=skill_data['description'],
                    category=skill_data['category'],
                    tags=skill_data.get('tags', []),
                    prerequisites=skill_data.get('prerequisites', []),
                    steps=skill_data['steps'],
                    examples=skill_data.get('examples', []),
                    status=skill_data.get('status', SkillStatus.ACTIVE.value),
                    version=skill_data.get('version', '1.0'),
                    created_by=skill_data.get('created_by', 'admin'),
                    owner_id=user_id,
                )
                
                # Save to database
                skill.save()
                
                # Add to vector store if active
                if skill.status == SkillStatus.ACTIVE.value:
                    vectorizer.add_skill(skill)
                
                imported_count += 1
                
            except Exception as e:
                errors.append(f"Error importing skill '{skill_data.get('name', 'unknown')}': {str(e)}")
        
        return jsonify({
            'success': True,
            'imported_count': imported_count,
            'total_provided': len(skills_data),
            'errors': errors,
            'message': f'Successfully imported {imported_count} skills'
        })
        
    except Exception as e:
        logger.error(f"Error importing skills: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500
