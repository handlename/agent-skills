import sys
import os
import re
import yaml

def parse_markdown(md_text):
    # Try to parse frontmatter
    frontmatter = {}
    content = md_text
    if md_text.startswith("---"):
        parts = md_text.split("---", 2)
        if len(parts) >= 3:
            try:
                frontmatter = yaml.safe_load(parts[1])
                content = parts[2]
            except Exception:
                pass
                
    return frontmatter, content

def md_to_html_components(content):
    # Replace Mermaid blocks
    def repl_mermaid(match):
        diagram = match.group(1).strip()
        # Clean up any figure captions inside if needed, or simple default caption
        caption = "Architecture & Flow Diagram"
        return f'<figure class="diagram-container"><div class="mermaid">\n{diagram}\n</div><figcaption>{caption}</figcaption></figure>'
        
    content = re.sub(r'```mermaid\s*(.*?)\s*```', repl_mermaid, content, flags=re.DOTALL)
    
    # Process simple priority badges
    content = re.sub(r'Must', '<span class="badge badge-must">Must</span>', content)
    content = re.sub(r'Should', '<span class="badge badge-should">Should</span>', content)
    content = re.sub(r'Could', '<span class="badge badge-could">Could</span>', content)
    content = re.sub(r'Wont', '<span class="badge badge-wont">Wont</span>', content)
    
    # Process status badges
    content = re.sub(r'Confirmed', '<span class="badge badge-confirmed">Confirmed</span>', content)
    content = re.sub(r'Inferred', '<span class="badge badge-inferred">Inferred</span>', content)
    content = re.sub(r'Assumption', '<span class="badge badge-assumption">Assumption</span>', content)
    
    # Convert Markdown tables to styled spec-tables
    def repl_table(match):
        table_text = match.group(0).strip()
        lines = [line.strip() for line in table_text.split("\n") if line.strip()]
        if len(lines) < 2:
            return table_text
            
        # Parse headers
        headers = [c.strip() for c in lines[0].split("|")[1:-1]]
        
        # Check alignment separator line (skip line 1)
        start_idx = 2
        
        html_table = '<table class="spec-table">\n<thead>\n<tr>\n'
        for h in headers:
            html_table += f'<th>{h}</th>\n'
        html_table += '</tr>\n</thead>\n<tbody>\n'
        
        for r_line in lines[start_idx:]:
            cols = [c.strip() for c in r_line.split("|")[1:-1]]
            html_table += '<tr>\n'
            for c in cols:
                # inline code wrap
                c = re.sub(r'`([^`]+)`', r'<code>\1</code>', c)
                html_table += f'<td>{c}</td>\n'
            html_table += '</tr>\n'
            
        html_table += '</tbody>\n</table>'
        return html_table
        
    table_pattern = r'(?:^\|[^\n]+\|\s*\n)(?:^\|[-:| ]+\|\s*\n)(?:^\|[^\n]+\|\s*\n?)+'
    content = re.sub(table_pattern, repl_table, content, flags=re.MULTILINE)
    
    # Convert simple callouts
    def repl_callout(match):
        c_type = match.group(1).lower()
        title = match.group(2).strip()
        body = match.group(3).strip()
        # Align style with callout tokens (info, warning, danger)
        if c_type == "note":
            c_type = "info"
        elif c_type == "important":
            c_type = "warning"
        elif c_type == "caution":
            c_type = "danger"
            
        return f'<div class="callout callout-{c_type}"><div class="callout-title">{title}</div><p>{body}</p></div>'
        
    callout_pattern = r'>\s*\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*\n>\s*\*\*([^\n*]+)\*\*\s*\n((?:>\s*[^\n]+\n?)+)'
    content = re.sub(callout_pattern, repl_callout, content, flags=re.IGNORECASE)
    
    return content

def resolve_output_path(workspace_dir, filename):
    output_dir = None
    gitignore_path = os.path.join(workspace_dir, ".gitignore")
    if os.path.exists(gitignore_path):
        with open(gitignore_path, "r", encoding="utf-8") as gf:
            lines = gf.readlines()
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            clean_line = line.rstrip("/")
            if clean_line in ["node_modules", ".git", ".gemini", "brain"]:
                continue
            if any(term in clean_line for term in ["dist", "build", "out", "tmp", "temp", "public", "docs"]):
                potential_dir = os.path.join(workspace_dir, clean_line)
                output_dir = potential_dir
                break

    if not output_dir:
        output_dir = os.path.join(workspace_dir, "tmp")

    os.makedirs(output_dir, exist_ok=True)
    basename = os.path.splitext(os.path.basename(filename))[0]
    return os.path.join(output_dir, f"{basename}.html")

def render_spec(input_path):
    workspace_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../"))
    template_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../references/template.html"))
    
    with open(input_path, "r", encoding="utf-8") as f:
        md_text = f.read()
        
    with open(template_path, "r", encoding="utf-8") as f:
        html_template = f.read()
        
    frontmatter, content = parse_markdown(md_text)
    
    # Metadata extraction
    title = frontmatter.get("title")
    if not title:
        # Fallback to first header
        first_h1 = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
        title = first_h1.group(1).strip() if first_h1 else "Specification Document"
        
    subtitle = frontmatter.get("subtitle", "System Technical Specification & Requirements")
    doc_type = frontmatter.get("type", "Technical Spec")
    version = frontmatter.get("version", "v1.0.0")
    date = frontmatter.get("date", "2026-05-30")
    audience = frontmatter.get("audience", "AI Agents & Engineers")
    source_file = os.path.basename(input_path)
    
    # Clean H1 from main body
    content = re.sub(r'^#\s+.+$', '', content, flags=re.MULTILINE).strip()
    
    # Parse standard structural headers for TOC
    h2_sections = re.findall(r'^##\s+(.+)$', content, re.MULTILINE)
    
    # Build a simple custom executive summary and summary cards
    exec_summary = "This visually rich document represents the compiled, interactive version of the technical specification. It integrates automated styling, dynamic badging, collapsible diagrams, and a local real-time review mode to enable closed-loop development."
    
    # Replace basic tokens
    html = html_template
    html = html.replace("{{TITLE}}", title)
    html = html.replace("{{SUBTITLE}}", subtitle)
    html = html.replace("{{DOC_TYPE}}", doc_type)
    html = html.replace("{{VERSION}}", version)
    html = html.replace("{{DATE}}", date)
    html = html.replace("{{AUDIENCE}}", audience)
    html = html.replace("{{SOURCE_FILE}}", source_file)
    html = html.replace("{{EXECUTIVE_SUMMARY}}", exec_summary)
    html = html.replace("{{LOGO}}", "📁")
    
    # Dynamic counts
    html = html.replace("{{COUNT}}", str(len(h2_sections)))
    html = html.replace("{{BREAKDOWN}}", "Auto-compiled specification section structure")
    
    # Simple markdown-to-html body parser
    processed_content = md_to_html_components(content)
    
    # Inject directly into doc-main
    # Since our template has separate structural sections, we can clean up standard structural sections 
    # or simple drop-in render for maximum compatibility.
    # To maintain premium layout, we'll replace the main body of the doc layout.
    doc_layout_match = re.search(r'<main class="doc-main">.*?</main>', html, flags=re.DOTALL)
    if doc_layout_match:
        # Wrap custom body inside doc-main
        parsed_body = f"""<main class="doc-main">
          <section id="main-content">
            {processed_content}
          </section>
        </main>"""
        html = re.sub(r'<main class="doc-main">.*?</main>', parsed_body, html, flags=re.DOTALL)
        
    # Rebuild TOC Sidebar
    toc_html = '<nav class="toc" aria-label="Table of contents">\n<div class="toc-title">Contents</div>\n<ol>\n'
    for sec in h2_sections:
        # Create anchor from section title
        anchor = re.sub(r'[^a-zA-Z0-9-]', '', sec.lower().replace(" ", "-"))
        toc_html += f'<li><a href="#{anchor}">{sec}</a></li>\n'
    toc_html += '</ol>\n</nav>'
    
    html = re.sub(r'<nav class="toc".*?</nav>', toc_html, html, flags=re.DOTALL)
    
    # Add id anchors to actual H2 headers in body
    def add_anchors(match):
        h_text = match.group(1).strip()
        anchor = re.sub(r'[^a-zA-Z0-9-]', '', h_text.lower().replace(" ", "-"))
        return f'<h2 id="{anchor}">{h_text}</h2>'
    html = re.sub(r'<h2>(.*?)</h2>', add_anchors, html)
    
    output_path = resolve_output_path(workspace_dir, input_path)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
        
    print(f"OUTPUT_RESOLVED: {output_path}")
    return output_path

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python render.py <input_spec_markdown>")
        sys.exit(1)
        
    render_spec(sys.argv[1])
