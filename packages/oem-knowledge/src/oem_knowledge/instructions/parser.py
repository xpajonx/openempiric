# TODO: migrate to canonical frontmatter parser
# (oem_knowledge.markdown.frontmatter) after wiki/concept/search/recovery
# paths are stabilized.

from __future__ import annotations
import re
import json
import hashlib
from pathlib import Path

# Directive keywords
DIRECTIVE_KEYWORDS = ["must", "never", "always", "do not", "before", "after", "required", "forbidden"]


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False

def parse_frontmatter(content: str) -> tuple[dict, str]:
    if not content.startswith("---"):
        return {}, content
    
    # Split on closing ---
    parts = content.split("---", 2)
    if len(parts) >= 3:
        fm_text = parts[1]
        remaining = parts[2]
        
        # Simple YAML-like parser
        fm_data = {}
        for line in fm_text.splitlines():
            line = line.strip()
            if not line or ":" not in line:
                continue
            k, v = line.split(":", 1)
            k = k.strip().lower()
            v = v.strip().strip("'\"")
            
            # Simple list parser if bracketed [a, b]
            if v.startswith("[") and v.endswith("]"):
                fm_data[k] = [item.strip().strip("'\"") for item in v[1:-1].split(",") if item.strip()]
            else:
                fm_data[k] = v
        return fm_data, remaining
    return {}, content

def extract_triggers(title: str, rule: str) -> list[str]:
    # Tokenize title and rule text
    text = (title + " " + rule).lower()
    words = re.findall(r"\b[a-zA-Z0-9_\-]{3,}\b", text)
    stop_words = {
        "the", "and", "for", "you", "that", "this", "with", "from", "your", "must", "never", "always",
        "rules", "rule", "instructions", "directive", "should", "shall", "does", "done", "will", "before",
        "after", "required", "forbidden", "about"
    }
    return sorted(list(set(w for w in words if w not in stop_words)))

def extract_file_globs(rule: str) -> list[str]:
    # Match patterns like *.py, src/**/*.ts, file.jsonc, AGENTS.md, etc.
    patterns = re.findall(r"\b[a-zA-Z0-9_\-\.\*\/]+\.[a-zA-Z0-9_\*]+\b", rule)
    return sorted(list(set(patterns)))

def parse_directives(source_path: str, content: str, source_hash: str) -> list[dict]:
    directives = []
    fm_data, body = parse_frontmatter(content)
    
    lines = body.splitlines()
    in_code_block = False
    current_section = "General"
    current_scope = "general"
    
    # Track overall project variables from frontmatter if defined
    fm_triggers = fm_data.get("triggers", [])
    fm_scope = fm_data.get("scope", None)
    fm_priority = fm_data.get("priority", None)
    fm_always_on = _as_bool(fm_data.get("always_on", False))
    
    line_number = content.count("\n") - len(lines)  # Offset line numbers due to frontmatter
    if line_number < 0:
        line_number = 0
        
    for i, line_str in enumerate(lines):
        line_number += 1
        stripped = line_str.strip()
        if not stripped:
            continue
            
        # Code block toggle
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue
            
        if in_code_block:
            continue
            
        # Heading match
        m_heading = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if m_heading:
            current_section = m_heading.group(2).strip()
            current_scope = re.sub(r"[^a-z0-9]+", "_", current_section.lower()).strip("_")
            continue
            
        # Check if line is a bullet/numbered list or contains directive keywords
        is_list_item = re.match(r"^([-\*\+]\s+(?:\[[ xX/]\]\s*)?|\d+\.\s+)(.+)$", stripped)
        
        has_keyword = False
        lower_stripped = stripped.lower()
        for kw in DIRECTIVE_KEYWORDS:
            if re.search(r"\b" + re.escape(kw) + r"\b", lower_stripped):
                has_keyword = True
                break
                
        if is_list_item or has_keyword:
            rule_text = is_list_item.group(2).strip() if is_list_item else stripped
            if len(rule_text) < 10:  # Skip trivial texts
                continue
                
            priority = "normal"
            if any(kw in rule_text.lower() for kw in ["must", "never", "required", "forbidden"]):
                priority = "critical"
                
            # Forbidden actions
            forbidden_actions = []
            if "never" in rule_text.lower() or "do not" in rule_text.lower():
                # Extract some verb-like actions following "never" or "do not"
                neg_match = re.search(r"(?:never|do not)\s+([a-zA-Z0-9_\-\s]+)", rule_text, re.IGNORECASE)
                if neg_match:
                    forbidden_actions.append(re.sub(r"\s+", "_", neg_match.group(1).strip().lower()))
                    
            # Extract file globs
            file_globs = extract_file_globs(rule_text)
            for fg in file_globs:
                forbidden_actions.append(fg)
                
            scope = fm_scope or current_scope or "general"
            priority = fm_priority or priority
            
            # Triggers
            triggers = extract_triggers(current_section, rule_text)
            if fm_triggers:
                triggers = sorted(list(set(triggers + fm_triggers)))
                
            normalized_text = rule_text.strip().lower()
            
            # Unique stable ID
            # hash(source_path + line_range + normalized_directive_text)
            line_range_str = f"{line_number}-{line_number}"
            id_input = f"{source_path}:{line_range_str}:{normalized_text}"
            stable_id = "directive_" + hashlib.sha256(id_input.encode("utf-8")).hexdigest()[:12]
            
            directives.append({
                "id": stable_id,
                "source_path": source_path,
                "source_hash": source_hash,
                "line_start": line_number,
                "line_end": line_number,
                "title": current_section,
                "scope": scope,
                "triggers": triggers,
                "priority": priority,
                "rule": rule_text,
                "forbidden_actions": forbidden_actions,
                "related_concepts": fm_data.get("related_concepts", []),
                "related_skills": fm_data.get("related_skills", []),
                "related_workflows": fm_data.get("related_workflows", []),
                "always_on": fm_always_on,
                "status": "active"
            })
            
    return directives
