"""
Template filters for Jinja2 templates.
Provides additional functionality for templates.
"""

def register_filters(app):
    """
    Register all custom filters for the Flask app
    
    Args:
        app: Flask application instance
    """
    @app.template_filter('max_value')
    def max_value_filter(a, b):
        """Return the maximum of two values"""
        return max(a, b)
    
    @app.template_filter('min_value')
    def min_value_filter(a, b):
        """Return the minimum of two values"""
        return min(a, b)