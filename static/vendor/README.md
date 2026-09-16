# Vendor Assets for Air-Gap Deployment

This directory contains all external CSS and JavaScript dependencies downloaded for air-gap deployment.

## Directory Structure

- `bootstrap/` - Bootstrap CSS and JS files (multiple versions)
- `fontawesome/` - Font Awesome CSS and font files
- `datatables/` - DataTables CSS and JS files
- `jquery/` - jQuery library
- `chartjs/` - Chart.js library
- `monaco-editor/` - Monaco Editor files (v0.55.1)
- `highlight/` - Highlight.js library and themes
- `markdown-it/` - Markdown-it parser
- `marked/` - Marked.js markdown parser
- `mermaid/` - Mermaid.js diagram rendering
- `jsplumb/` - jsPlumb assets (workflow editor backup)

## Usage

All templates have been updated to reference these local files instead of CDN URLs.
The application should now work in an air-gapped environment.
