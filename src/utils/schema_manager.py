import json
import os
import logging
from typing import Dict, List, Optional, Any, Tuple

class SchemaManager:
    """Manager class for handling database schema from a JSON file"""
    
    def __init__(self, schema_file_path=None, condition_file_path=None):
        """Initialize the schema manager with paths to the schema JSON and condition JSON files
        
        Args:
            schema_file_path (str, optional): Path to schema.json file, defaults to config/data/schema.json
            condition_file_path (str, optional): Path to condition.json file, defaults to config/data/condition.json
        """
        self.logger = logging.getLogger('text2sql.schema_manager')
        
        # Initialize base directory path
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        
        if schema_file_path is None:
            # Default path is relative to the project root
            schema_file_path = os.path.join(base_dir, 'config', 'data', 'schema.json')
        
        if condition_file_path is None:
            # Default path for condition.json is also in config/data
            condition_file_path = os.path.join(base_dir, 'config', 'data', 'condition.json')
        
        self.schema_file_path = schema_file_path
        self.condition_file_path = condition_file_path
        
        self.schema_data = None
        self.condition_data = None
        self.workspaces = []
        self._joins = []
        self._joins_loaded = False

        # The schema is what every caller needs; join conditions live in a
        # separate file and are read on first use, so a caller that only lists
        # workspaces or tables never pays for - or depends on - condition.json.
        self.load_schema()

    @property
    def joins(self) -> List[Dict[str, Any]]:
        """Join conditions, read from condition.json on first access."""
        self._ensure_joins_loaded()
        return self._joins

    @joins.setter
    def joins(self, value: List[Dict[str, Any]]) -> None:
        self._joins = value

    def _ensure_joins_loaded(self) -> None:
        """Read condition.json once, on first use."""
        if not self._joins_loaded:
            self.load_join_conditions()
    
    def load_schema(self) -> bool:
        """Load schema data from the JSON file
        
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            self.logger.info(f"Loading schema from {self.schema_file_path}")
            with open(self.schema_file_path, 'r') as f:
                self.schema_data = json.load(f)
                
            # Extract workspaces for easier access
            if self.schema_data and 'workspaces' in self.schema_data:
                self.workspaces = self.schema_data['workspaces']
                self.logger.info(f"Loaded {len(self.workspaces)} workspaces from schema file")
                return True
            else:
                self.logger.error("Schema file does not contain 'workspaces' key")
                return False
                
        except Exception as e:
            self.logger.error(f"Error loading schema file: {str(e)}", exc_info=True)
            return False
    
    def get_workspaces(self) -> List[Dict[str, Any]]:
        """Get all available workspaces
        
        Returns:
            List[Dict]: List of workspace dictionaries
        """
        workspace_list = []
        for workspace in self.workspaces:
            workspace_list.append({
                "name": workspace.get("name", "Unnamed"),
                "description": workspace.get("description", "")
            })
        return workspace_list
    
    def get_workspace_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Get a specific workspace by name
        
        Args:
            name (str): Name of the workspace to retrieve
            
        Returns:
            Dict or None: Workspace data if found, None otherwise
        """
        for workspace in self.workspaces:
            if workspace.get("name") == name:
                return workspace
        return None
    
    def get_tables(self, workspace_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get all tables for a specific workspace or across all workspaces
        
        Args:
            workspace_name (str, optional): Name of workspace to get tables from
            
        Returns:
            List[Dict]: List of table dictionaries
        """
        tables = []
        
        if workspace_name:
            # Get tables from specific workspace
            workspace = self.get_workspace_by_name(workspace_name)
            if workspace and "tables" in workspace:
                tables.extend(workspace["tables"])
        else:
            # Get tables from all workspaces
            for workspace in self.workspaces:
                if "tables" in workspace:
                    tables.extend(workspace["tables"])
        
        return tables
    
    def get_table_names(self, workspace_name: Optional[str] = None) -> List[str]:
        """Get all table names for a specific workspace or across all workspaces
        
        Args:
            workspace_name (str, optional): Name of workspace to get table names from
            
        Returns:
            List[str]: List of table names
        """
        tables = self.get_tables(workspace_name)
        return [table["name"] for table in tables if "name" in table]
    
    def get_table_by_name(self, table_name: str, workspace_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Get a specific table by name
        
        Args:
            table_name (str): Name of the table to retrieve
            workspace_name (str, optional): Name of workspace to search in
            
        Returns:
            Dict or None: Table data if found, None otherwise
        """
        tables = self.get_tables(workspace_name)
        
        for table in tables:
            if table.get("name") == table_name:
                return table
        
        return None
    
    def get_columns(self, table_name: str, workspace_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get all columns for a specific table
        
        Args:
            table_name (str): Name of the table to get columns from
            workspace_name (str, optional): Name of workspace to search in
            
        Returns:
            List[Dict]: List of column dictionaries
        """
        table = self.get_table_by_name(table_name, workspace_name)
        
        if table and "columns" in table:
            return table["columns"]
        
        return []
    
    def get_primary_keys(self, table_name: str, workspace_name: Optional[str] = None) -> List[str]:
        """Get primary key column names for a specific table
        
        Args:
            table_name (str): Name of the table to get primary keys from
            workspace_name (str, optional): Name of workspace to search in
            
        Returns:
            List[str]: List of primary key column names
        """
        columns = self.get_columns(table_name, workspace_name)
        
        primary_keys = []
        for column in columns:
            if column.get("is_primary_key", False):
                primary_keys.append(column.get("name"))
        
        return primary_keys
    
    def format_schema_for_display(self, workspace_name: Optional[str] = None, tables: Optional[List[str]] = None) -> str:
        """Format the schema information as a string for display
        
        Args:
            workspace_name (str, optional): Name of workspace to get schema from
            tables (List[str], optional): List of specific tables to include
            
        Returns:
            str: Formatted schema information
        """
        schema_info = []
        
        # Get all tables or filtered by workspace/table list
        all_tables = self.get_tables(workspace_name)
        
        # Filter tables if a specific list is provided
        if tables:
            filtered_tables = []
            for table in all_tables:
                if table.get("name") in tables:
                    filtered_tables.append(table)
            all_tables = filtered_tables
        
        # Format each table's information
        for table in all_tables:
            table_name = table.get("name", "Unknown")
            schema_info.append(f"Table: {table_name}")
            
            # Add description if available
            if "description" in table:
                schema_info.append(f"Description: {table['description']}")
            
            # Format columns with descriptions
            if "columns" in table:
                column_strs = []
                primary_keys = []
                
                for column in table["columns"]:
                    col_name = column.get("name", "Unknown")
                    col_type = column.get("datatype", "Unknown")
                    col_desc = column.get("description", "")
                    column_strs.append(f"{col_name} ({col_type}) - {col_desc}")
                    
                    # Track primary keys
                    if column.get("is_primary_key", False):
                        primary_keys.append(col_name)
                
                schema_info.append("Columns: " + ", ".join(column_strs))
                
                # Add primary key information
                if primary_keys:
                    schema_info.append(f"Primary Key(s): {', '.join(primary_keys)}")
            
            schema_info.append("")
        
        return "\n".join(schema_info)
    
    def save_schema(self, schema_data=None) -> bool:
        """Save schema data back to the JSON file
        
        Args:
            schema_data (dict, optional): New schema data to save, uses current data if None
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            data_to_save = schema_data or self.schema_data
            
            if not data_to_save:
                self.logger.error("No schema data to save")
                return False
                
            self.logger.info(f"Saving schema to {self.schema_file_path}")
            
            with open(self.schema_file_path, 'w') as f:
                json.dump(data_to_save, f, indent=2)
                
            return True
            
        except Exception as e:
            self.logger.error(f"Error saving schema file: {str(e)}", exc_info=True)
            return False
    
    def load_join_conditions(self) -> bool:
        """Load join conditions from the JSON file
        
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            self.logger.info(f"Loading join conditions from {self.condition_file_path}")
            with open(self.condition_file_path, 'r') as f:
                self.condition_data = json.load(f)
                
            # Extract joins for easier access
            if self.condition_data and 'joins' in self.condition_data:
                self._joins = self.condition_data['joins']
                self.logger.info(f"Loaded {len(self._joins)} join conditions from condition file")
                return True
            else:
                self.logger.error("Condition file does not contain 'joins' key")
                return False
                
        except Exception as e:
            self.logger.error(f"Error loading condition file: {str(e)}", exc_info=True)
            return False
        finally:
            # Mark the read as attempted so a failed load is not retried on every
            # attribute access. An explicit call still re-reads the file.
            self._joins_loaded = True
            
    def get_join_conditions(self, tables: List[str], workspace_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get join conditions for a set of tables
        
        Args:
            tables (List[str]): List of table names to find join conditions for
            workspace_name (str, optional): Name of workspace to filter by
            
        Returns:
            List[Dict]: List of join conditions relevant to the provided tables
        """
        if not tables or len(tables) < 2:
            return []
            
        relevant_joins = []
        
        for join in self.joins:
            left_table = join.get("left_table", "")
            right_table = join.get("right_table", "")
            
            # Check if both tables in the join are in our list of tables
            if left_table in tables and right_table in tables:
                relevant_joins.append(join)
                
        return relevant_joins
        
    def has_join_condition(self, table1: str, table2: str) -> bool:
        """Check if a join condition exists between two specific tables
        
        Args:
            table1 (str): First table name
            table2 (str): Second table name
            
        Returns:
            bool: True if a join condition exists, False otherwise
        """
        for join in self.joins:
            left_table = join.get("left_table", "")
            right_table = join.get("right_table", "")
            
            # Check both directions
            if (left_table == table1 and right_table == table2) or (left_table == table2 and right_table == table1):
                return True
                
        return False
        
    def get_specific_join(self, table1: str, table2: str) -> Optional[Dict[str, Any]]:
        """Get the specific join condition between two tables
        
        Args:
            table1 (str): First table name
            table2 (str): Second table name
            
        Returns:
            Dict or None: Join condition if found, None otherwise
        """
        for join in self.joins:
            left_table = join.get("left_table", "")
            right_table = join.get("right_table", "")
            
            # Match in either direction
            if (left_table == table1 and right_table == table2) or (left_table == table2 and right_table == table1):
                return join
                
        return None
        
    def save_join_conditions(self, condition_data=None) -> bool:
        """Save join condition data back to the JSON file
        
        Args:
            condition_data (dict, optional): New condition data to save, uses current data if None
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            # Saving without an explicit payload must not clobber the file with an
            # empty dict when lazy loading has not happened yet.
            self._ensure_joins_loaded()
            data_to_save = condition_data or self.condition_data
            
            if not data_to_save:
                self.logger.error("No join condition data to save")
                return False
                
            self.logger.info(f"Saving join conditions to {self.condition_file_path}")
            
            with open(self.condition_file_path, 'w') as f:
                json.dump(data_to_save, f, indent=2)
                
            return True
            
        except Exception as e:
            self.logger.error(f"Error saving join condition file: {str(e)}", exc_info=True)
            return False