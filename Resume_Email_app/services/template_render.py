from jinja2 import Environment, FileSystemLoader
import os

template_env = Environment(
    loader=FileSystemLoader([
        os.path.join(os.path.dirname(__file__), '../templates/resume'),
        os.path.join(os.path.dirname(__file__), '../templates/emails')
    ]),
    autoescape=True,
    trim_blocks=True,
    lstrip_blocks=True
)

def render_resume(template_name: str, context: dict) -> str:
    """Render resume template with provided data"""
    template = template_env.get_template(f"{template_name}.tex")
    return template.render(**context)

def render_email(template_name: str, context: dict) -> str:
    """Render email template with provided data"""
    template = template_env.get_template(f"emails/{template_name}.md")
    return template.render(**context)