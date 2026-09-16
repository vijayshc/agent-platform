"""
User, Role, and Permission models for Text2SQL application
"""

from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Table
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
import datetime

Base = declarative_base()

# Association tables for many-to-many relationships
user_role_association = Table(
    'user_roles',
    Base.metadata,
    Column('user_id', Integer, ForeignKey('users.id')),
    Column('role_id', Integer, ForeignKey('roles.id'))
)

role_permission_association = Table(
    'role_permissions',
    Base.metadata,
    Column('role_id', Integer, ForeignKey('roles.id')),
    Column('permission_id', Integer, ForeignKey('permissions.id'))
)

class User(Base):
    """User model for authentication and permissions"""
    __tablename__ = 'users'
    
    id = Column(Integer, primary_key=True)
    username = Column(String(50), unique=True, nullable=False)
    email = Column(String(100), unique=True, nullable=False)
    password_hash = Column(String(200), nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
    
    # Reset token fields
    reset_token = Column(String(100), nullable=True)
    reset_token_expiry = Column(DateTime, nullable=True)
    
    # Relationships
    roles = relationship(
        'Role',
        secondary=user_role_association,
        back_populates='users'
    )

    def __repr__(self):
        return f"<User {self.username}>"
        
class Role(Base):
    """Role model for user roles (admin, user, etc.)"""
    __tablename__ = 'roles'
    
    id = Column(Integer, primary_key=True)
    name = Column(String(50), unique=True, nullable=False)
    description = Column(String(255))
    
    # Relationships
    users = relationship(
        'User',
        secondary=user_role_association,
        back_populates='roles'
    )
    
    permissions = relationship(
        'Permission',
        secondary=role_permission_association,
        back_populates='roles'
    )
    
    def __repr__(self):
        return f"<Role {self.name}>"

class Permission(Base):
    """Permission model for specific access rights"""
    __tablename__ = 'permissions'
    
    id = Column(Integer, primary_key=True)
    name = Column(String(100), unique=True, nullable=False)
    description = Column(String(255))
    
    # Relationships
    roles = relationship(
        'Role',
        secondary=role_permission_association,
        back_populates='permissions'
    )
    
    def __repr__(self):
        return f"<Permission {self.name}>"

class UserRole:
    """Constants for default user roles"""
    ADMIN = "admin"
    USER = "user"

# Define constants for permissions
class Permissions:
    """Constants for all permissions in the system"""
    # General permissions
    VIEW_INDEX = "view_index"
    
    # SQL query permissions
    RUN_QUERIES = "run_queries"
    
    # Sample management permissions
    VIEW_SAMPLES = "view_samples"
    MANAGE_SAMPLES = "manage_samples"
    
    # Schema permissions
    VIEW_SCHEMA = "view_schema"
    MANAGE_SCHEMA = "manage_schema"
    
    # Knowledge base permissions
    VIEW_KNOWLEDGE = "view_knowledge"
    USE_KNOWLEDGE = "use_knowledge"
    MANAGE_KNOWLEDGE = "manage_knowledge"
    
    # Admin permissions
    ADMIN_ACCESS = "admin_access"
    MANAGE_USERS = "manage_users"
    MANAGE_ROLES = "manage_roles"
    MANAGE_CONFIG = "manage_config"