from __future__ import annotations

import json
import re
import sys
import time
import subprocess

def _strip_jsonc_comments(text: str) -> str:
    """Safely strip JSONC comments without destroying comments/slashes inside string literals (like URLs)."""
    pattern = re.compile(r'("(?:\\.|[^"\\])*")|//[^\r\n]*|/\*[\s\S]*?\*/')
    return pattern.sub(lambda m: m.group(1) if m.group(1) else "", text)


def _update_jsonc_mcp(original_text: str, mcp_config: dict) -> str:
    cleaned = _strip_jsonc_comments(original_text)
    config_data = json.loads(cleaned, strict=False)
    
    # Identify spans of all comments
    comment_spans = []
    for m_comment in re.finditer(r'//[^\r\n]*|/\*[\s\S]*?\*/', original_text):
        comment_spans.append(m_comment.span())
    
    def in_comment(pos):
        return any(start <= pos < end for start, end in comment_spans)
        
    new_text = original_text

    # Safe cleanup of legacy oem server key if OEM-managed
    has_oem = "oem" in config_data.get("mcp", {})
    if has_oem:
        oem_entry = config_data["mcp"]["oem"]
        cmd_str = str(oem_entry.get("command", ""))
        args_str = str(oem_entry.get("args", []))
        is_oem_managed = (
            "oem" in cmd_str.lower() or 
            "openempiric" in cmd_str.lower() or 
            "oem_knowledge" in cmd_str.lower() or
            "oem" in args_str.lower() or
            "openempiric" in args_str.lower() or
            "oem_knowledge" in args_str.lower()
        )
        if is_oem_managed:
            match_oem = None
            for m in re.finditer(r'"oem"\s*:\s*\{', new_text):
                if not in_comment(m.start()):
                    match_oem = m
                    break
            if match_oem:
                start_pos = match_oem.start()
                brace_start = match_oem.end() - 1
                depth = 1
                brace_end = -1
                for idx in range(brace_start + 1, len(new_text)):
                    if new_text[idx] == '{':
                        depth += 1
                    elif new_text[idx] == '}':
                        depth -= 1
                        if depth == 0:
                            brace_end = idx
                            break
                if brace_end != -1:
                    rest = new_text[brace_end + 1:]
                    trailing_comma = re.match(r'\s*,', rest)
                    if trailing_comma:
                        end_pos = brace_end + 1 + trailing_comma.end()
                        new_text = new_text[:start_pos] + new_text[end_pos:]
                    else:
                        before = new_text[:start_pos]
                        leading_comma = re.search(r',\s*$', before)
                        if leading_comma:
                            start_pos = leading_comma.start()
                        new_text = new_text[:start_pos] + new_text[brace_end + 1:]
                    
                    # Re-evaluate config data and comment spans after mutation
                    cleaned = _strip_jsonc_comments(new_text)
                    config_data = json.loads(cleaned, strict=False)
                    comment_spans = []
                    for m_comment in re.finditer(r'//[^\r\n]*|/\*[\s\S]*?\*/', new_text):
                        comment_spans.append(m_comment.span())
        else:
            print("Warning: Custom user MCP server named 'oem' detected. Preserving user configuration.")

    # Check if config already has correct MCP config
    existing_mcp = config_data.get("mcp", {}).get("openempiric")
    if existing_mcp == mcp_config:
        return new_text
        
    # 1. Find "mcp" key
    match_mcp = None
    for m in re.finditer(r'"mcp"\s*:\s*\{', new_text):
        if not in_comment(m.start()):
            match_mcp = m
            break
            
    if match_mcp:
        # "mcp" key exists, look for "openempiric"
        has_oe = "openempiric" in config_data.get("mcp", {})
        if has_oe:
            # Replace existing "openempiric" config
            match_oe = None
            for m in re.finditer(r'"openempiric"\s*:\s*\{', new_text):
                if not in_comment(m.start()):
                    match_oe = m
                    break
            if match_oe:
                start_pos = match_oe.start()
                brace_start = match_oe.end() - 1
                if brace_start != -1:
                    depth = 1
                    brace_end = -1
                    for idx in range(brace_start + 1, len(new_text)):
                        if new_text[idx] == '{':
                            depth += 1
                        elif new_text[idx] == '}':
                            depth -= 1
                            if depth == 0:
                                brace_end = idx
                                break
                    if brace_end != -1:
                        serialized_oe = f'"openempiric": {json.dumps(mcp_config, indent=4).replace("\n", "\n    ")}'
                        new_text = new_text[:start_pos] + serialized_oe + new_text[brace_end + 1:]
        else:
            # Insert "openempiric" at the start of the "mcp" object
            pos = match_mcp.end()
            serialized_oe = f'\n    "openempiric": {json.dumps(mcp_config, indent=4).replace("\n", "\n    ")},'
            new_text = new_text[:pos] + serialized_oe + new_text[pos:]
    else:
        # "mcp" key does not exist. Append it before the last closing brace
        r_pos = new_text.rfind('}')
        if r_pos != -1:
            before_brace = new_text[:r_pos]
            last_char_match = re.search(r'\S\s*$', before_brace)
            comma = ""
            if last_char_match:
                last_char = last_char_match.group(0).strip()
                if last_char not in ("{", ",", "["):
                    comma = ","
            mcp_serialized = json.dumps({"openempiric": mcp_config}, indent=4).replace("\n", "\n  ")
            new_entry = f'{comma}\n  "mcp": {mcp_serialized}\n'
            new_text = new_text[:r_pos] + new_entry + new_text[r_pos:]
            
    return new_text


def check_mcp_server(command: list[str]) -> tuple[bool, bool, int, str]:
    """Test standard I/O MCP server reachability and functionality.

    Returns:
        (reachable, functional, num_tools, error_message)
    """
    import select
    
    try:
        proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1
        )
        
        def _get_stderr_log() -> str:
            try:
                import os
                fd = proc.stderr.fileno()
                fl = os.fcntl(fd, os.F_GETFL)
                os.fcntl(fd, os.F_SETFL, fl | os.O_NONBLOCK)
                content = proc.stderr.read() or ""
                return content.strip()
            except Exception:
                return ""
        
        # 1. Reachability check: Send initialize
        init_req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "oem-doctor", "version": "1.0"}
            }
        }
        proc.stdin.write(json.dumps(init_req) + "\n")
        proc.stdin.flush()
        
        # Increase initial connection timeout to 10.0 seconds to allow interpreter startup
        ready = select.select([proc.stdout], [], [], 10.0)
        if not ready[0]:
            stderr_log = _get_stderr_log()
            proc.kill()
            err_msg = "Timeout waiting for initialize response"
            if stderr_log:
                err_msg += f". Server stderr:\n{stderr_log}"
            return False, False, 0, err_msg
            
        init_resp_line = proc.stdout.readline()
        if not init_resp_line:
            stderr_log = _get_stderr_log()
            proc.kill()
            err_msg = "Empty response on initialize"
            if stderr_log:
                err_msg += f". Server stderr:\n{stderr_log}"
            return False, False, 0, err_msg
            
        # 2. Tool count check: Send tools/list
        tools_req = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list"
        }
        proc.stdin.write(json.dumps(tools_req) + "\n")
        proc.stdin.flush()
        
        # Subsequent requests take 5.0 seconds max
        ready = select.select([proc.stdout], [], [], 5.0)
        if not ready[0]:
            stderr_log = _get_stderr_log()
            proc.kill()
            err_msg = "Timeout waiting for tools/list response"
            if stderr_log:
                err_msg += f". Server stderr:\n{stderr_log}"
            return True, False, 0, err_msg
            
        tools_resp_line = proc.stdout.readline()
        if not tools_resp_line:
            stderr_log = _get_stderr_log()
            proc.kill()
            err_msg = "Empty response on tools/list"
            if stderr_log:
                err_msg += f". Server stderr:\n{stderr_log}"
            return True, False, 0, err_msg
            
        tools_resp = json.loads(tools_resp_line)
        if "error" in tools_resp:
            proc.kill()
            return True, False, 0, f"Error from tools/list: {tools_resp['error']}"
            
        tools_list = tools_resp.get("result", {}).get("tools", [])
        num_tools = len(tools_list)
        
        # 3. Functional check: Send call tool knowledge_stats
        call_req = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "knowledge_stats",
                "arguments": {}
            }
        }
        proc.stdin.write(json.dumps(call_req) + "\n")
        proc.stdin.flush()
        
        ready = select.select([proc.stdout], [], [], 5.0)
        if not ready[0]:
            stderr_log = _get_stderr_log()
            proc.kill()
            err_msg = "Timeout waiting for tool call response"
            if stderr_log:
                err_msg += f". Server stderr:\n{stderr_log}"
            return True, False, num_tools, err_msg
            
        call_resp_line = proc.stdout.readline()
        proc.kill()
        
        if not call_resp_line:
            stderr_log = _get_stderr_log()
            err_msg = "Empty response on tool call"
            if stderr_log:
                err_msg += f". Server stderr:\n{stderr_log}"
            return True, False, num_tools, err_msg
            
        call_resp = json.loads(call_resp_line)
        if "error" in call_resp:
            return True, False, num_tools, f"Error calling knowledge_stats: {call_resp['error']}"
            
        content = call_resp.get("result", {}).get("content", [])
        if not content:
            return True, False, num_tools, "No content returned from knowledge_stats call"
            
        return True, True, num_tools, ""
    except Exception as e:
        return False, False, 0, str(e)


class Spinner:
    def __init__(self, message="Checking environment..."):
        self.message = message
        self.spinner_chars = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        import threading
        self.stop_running = threading.Event()
        self.thread = None
        self.enabled = sys.stdout.isatty()

    def _spin(self):
        if not self.enabled:
            return
        idx = 0
        while not self.stop_running.is_set():
            char = self.spinner_chars[idx % len(self.spinner_chars)]
            sys.stdout.write(f"\r\033[96m{char}\033[0m {self.message}")
            sys.stdout.flush()
            time.sleep(0.08)
            idx += 1
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()

    def __enter__(self):
        if self.enabled:
            import threading
            self.thread = threading.Thread(target=self._spin, daemon=True)
            self.thread.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.enabled:
            self.stop_running.set()
            if self.thread:
                self.thread.join()
