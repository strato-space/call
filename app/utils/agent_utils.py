"""
Utility functions for working with AI agent profiles.
"""
import re
from typing import Dict, Any, Optional, List, Union
from pathlib import Path
from dataclasses import dataclass, field
from enum import Enum

def extract_agent_attributes(file_path: str) -> Dict[str, Any]:
    """
    Extract YAML attributes from an agent profile file.
    
    Args:
        file_path: Path to the agent profile file
        
    Returns:
        Dictionary containing the extracted attributes, or empty dict if not found
        
    Example:
        attributes = extract_agent_attributes("path/to/agent.md")
        print(attributes.get("name"))  # e.g., "UralAirNewsRadar"
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # If it's a pure YAML file, parse directly
        if str(file_path).lower().endswith(('.yaml', '.yml')):
            try:
                import yaml  # lazy import
                data = yaml.safe_load(content) or {}
                if isinstance(data, dict):
                    # Return merged attributes including raw content for reference
                    merged = {"content": content, "yaml": content}
                    merged.update(data)
                    return merged
            except Exception:
                # Fall through to fenced-block parsing below
                pass

        # Match YAML block between ```yaml and ``` or ```Yaml and ```
        yaml_pattern = r'```(?:yaml|Yaml|YAML)?\n(.*?)\n```'
        match = re.search(yaml_pattern, content, re.DOTALL)
        
        if not match:
            # As a fallback, try to parse the whole file as YAML (when markdown fences are not used)
            try:
                import yaml  # lazy import
                data = yaml.safe_load(content) or {}
                if isinstance(data, dict):
                    merged = {"content": content, "yaml": content}
                    merged.update(data)
                    return merged
            except Exception:
                # Provide path and a sanitized short preview to help diagnose
                import re as _re
                sanitized = _re.sub(r"\s+", " ", content).strip()
                preview = (sanitized[:200] + ('...' if len(sanitized) > 200 else ''))
                print(f"No YAML block found in file: {file_path}\nPreview: {preview}")
                return {}
            
        yaml_content = match.group(1)
        
        # Initialize attributes with content and yaml first
        attributes = {
            "content": content,
            "yaml": yaml_content
        }
        
        # Simple YAML parser for key-value pairs with better handling of multi-line values
        lines = [line.rstrip() for line in yaml_content.split('\n')]
        current_key = None
        current_value = []
        in_multiline = False
        
        for line in lines:
            # Skip empty lines if not in a multi-line value
            if not line.strip() and not in_multiline:
                continue
                
            # Check for key-value pair
            if ':' in line and not in_multiline:
                # Save previous key-value pair if exists
                if current_key and current_value:
                    attributes[current_key] = '\n'.join(current_value).strip()
                    current_value = []
                
                # Parse new key-value pair
                key, value = line.split(':', 1)
                current_key = key.strip()
                value = value.strip()
                
                # Check for multi-line value
                if value in ['>', '|'] or (value == '' and line.endswith(':')):
                    in_multiline = True
                    current_value = []
                else:
                    attributes[current_key] = value
            else:
                # Handle multi-line values
                if current_key:
                    # Check for end of multi-line value
                    if line.strip() == '' and in_multiline:
                        attributes[current_key] = '\n'.join(current_value).strip()
                        in_multiline = False
                        current_value = []
                    else:
                        # Clean up indentation for multi-line values
                        clean_line = line.strip()
                        if clean_line:
                            current_value.append(clean_line)
        
        # Add the last key-value pair if any
        if current_key and current_value:
            attributes[current_key] = '\n'.join(current_value).strip()
        
        # Clean up values
        for key, value in attributes.items():
            if isinstance(value, str):
                # Remove trailing colons and extra whitespace
                attributes[key] = value.rstrip(':')
        
        return attributes
        
    except Exception as e:
        print(f"Error extracting agent attributes: {e}")
        import traceback
        traceback.print_exc()
        return {}

def get_agent_instructions(file_path: str, template_vars: Optional[Dict[str, str]] = None) -> str:
    """
    Get instructions from an agent profile, with optional variable substitution.
    
    Args:
        file_path: Path to the agent profile file
        template_vars: Dictionary of variables to substitute in the instructions
        
    Returns:
        Agent instructions with variables substituted
    """
    attributes = extract_agent_attributes(file_path)
    instructions = attributes.get('instructions', '')
    
    if not instructions and 'role' in attributes and 'goal' in attributes:
        instructions = f"Role: {attributes['role']}\n\nGoal: {attributes['goal']}"
    
    if template_vars:
        for key, value in template_vars.items():
            placeholder = f'{{{{{key}}}}}'  # Double braces for the template
            instructions = instructions.replace(placeholder, value)
    
    return instructions


class FieldType(Enum):
    """Field types for Strato.Space AI Prompt Framework."""
    GOAL = "goal"
    ROLE = "role"
    INSTRUCTIONS = "instructions"
    REASONING_METHOD = "reasoning_method"
    CONTEXT = "context"
    DOMAIN_MODEL = "domain_model"
    INPUT_FORMAT = "input_format"
    OUTPUT_FORMAT = "output_format"
    EXAMPLE = "example"
    CONSTRAINT = "constraint"
    LOCALIZATION = "localization"


@dataclass
class AgentProfile:
    """Compact representation of an agent profile using Strato.Space AI Prompt Framework."""
    name: str = ""
    goal: str = ""
    role: str = ""
    instructions: str = ""
    reasoning_method: str = ""
    context: Dict[str, Any] = field(default_factory=dict)
    domain_model: Dict[str, Any] = field(default_factory=dict)
    input_format: str = ""
    output_format: str = ""
    examples: List[Dict[str, str]] = field(default_factory=list)
    constraints: List[str] = field(default_factory=list)
    localization: Dict[str, str] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert the agent profile to a dictionary."""
        return {
            "name": self.name,
            "goal": self.goal,
            "role": self.role,
            "instructions": self.instructions,
            "reasoning_method": self.reasoning_method,
            "context": self.context,
            "domain_model": self.domain_model,
            "input_format": self.input_format,
            "output_format": self.output_format,
            "examples": self.examples,
            "constraints": self.constraints,
            "localization": self.localization
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'AgentProfile':
        """Create an AgentProfile from a dictionary."""
        return cls(**{
            k: v for k, v in data.items() 
            if k in cls.__annotations__
        })


def extract_strato_fields(file_path: Union[str, Path]) -> AgentProfile:
    """
    Extract Strato.Space AI Prompt Framework fields from an agent profile.
    
    This is a more structured and compact alternative to extract_agent_attributes
    that specifically handles the the Strato.Space AI Prompt Framework fields.
    
    Args:
        file_path: Path to the agent profile file
        
    Returns:
        AgentProfile object with extracted fields
    """
    # First get all attributes using the existing extractor
    attrs = extract_agent_attributes(str(file_path))
    if not attrs:
        return AgentProfile()
    
    profile = AgentProfile()
    
    # Direct mappings
    direct_fields = [
        ('name', 'name'),
        ('goal', 'goal'),
        ('role', 'role'),
        ('instructions', 'instructions'),
        ('reasoning_method', 'reasoning_method'),
        ('input_format', 'input_format'),
        ('output_format', 'output_format')
    ]
    
    for field_name, attr_name in direct_fields:
        if attr_name in attrs:
            setattr(profile, field_name, attrs[attr_name])
    
    # Handle context and domain model
    if 'context' in attrs and isinstance(attrs['context'], dict):
        profile.context = attrs['context']
        
        # Extract domain model from context if present
        if 'domain_model' in profile.context:
            profile.domain_model = profile.context['domain_model']
    
    # Handle examples (can be a list or a single example)
    if 'examples' in attrs:
        if isinstance(attrs['examples'], list):
            profile.examples = attrs['examples']
        else:
            profile.examples = [{"example": attrs['examples']}]
    
    # Handle constraints (can be a list or a single constraint)
    if 'constraints' in attrs:
        if isinstance(attrs['constraints'], list):
            profile.constraints = attrs['constraints']
        else:
            profile.constraints = [attrs['constraints']]
    
    # Handle localization (should be a dict)
    if 'localization' in attrs and isinstance(attrs['localization'], dict):
        profile.localization = attrs['localization']
    
    return profile


def get_field(file_path: Union[str, Path], field_type: 'FieldType') -> Any:
    """
    Get a specific field from an agent profile using Strato.Space AI Prompt Framework.
    
    Args:
        file_path: Path to the agent profile file
        field_type: Type of field to extract
        
    Returns:
        The value of the requested field, or None if not found
    """
    profile = extract_strato_fields(file_path)
    field_name = field_type.value
    
    # Special handling for nested fields
    if field_type == FieldType.DOMAIN_MODEL:
        return profile.domain_model
    
    return getattr(profile, field_name, None)
