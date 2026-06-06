from __future__ import annotations

# Styles/Colors
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
MAGENTA = "\033[95m"
CYAN = "\033[96m"
WHITE = "\033[97m"


def status_tag(status: str) -> str:
    s = status.upper()
    if s in ("OK", "SUCCESS", "GREEN"):
        return f"{GREEN} SUCCESS{RESET}"
    elif s in ("SEARCH", "SEARCHING", "FIND"):
        return f"{BLUE} SEARCHING{RESET}"
    elif s in ("WRITE", "WRITING", "SAVE"):
        return f"{CYAN} WRITING{RESET}"
    elif s in ("STATS", "INFO"):
        return f"{MAGENTA} STATS{RESET}"
    elif s in ("INDEX", "BUILD"):
        return f"{YELLOW} INDEXING{RESET}"
    elif s in ("BOOTSTRAP", "INIT", "SETUP"):
        return f"{YELLOW} BOOTSTRAPPING{RESET}"
    elif s in ("PLAN", "PLANNING"):
        return f"{BLUE} PLANNING{RESET}"
    elif s in ("EXECUTE", "EXEC", "RUN"):
        return f"{GREEN} EXECUTING{RESET}"
    elif s in ("ERROR", "FAIL", "FAILURE"):
        return f"{RED} ERROR{RESET}"
    return f"{CYAN} {status.upper()}{RESET}"


def render_panel(title: str, lines: list, status: str = "OK", width: int = 72) -> str:
    border_top = "╔" + "═" * (width - 2) + "╗"
    border_bottom = "╚" + "═" * (width - 2) + "╝"

    tag = status_tag(status)
    header_text = f"  {title} | {tag}  "
    if len(header_text) < width - 6:
        padding_left = (width - 2 - len(header_text)) // 2
        padding_right = width - 2 - len(header_text) - padding_left
        border_top = "╔" + "═" * padding_left + header_text + "═" * padding_right + "╗"

    panel_lines = [border_top]
    for line in lines:
        line_str = str(line)
        while len(line_str) > width - 4:
            chunk = line_str[: width - 4]
            panel_lines.append(f"║ {chunk.ljust(width - 4)} ║")
            line_str = line_str[width - 4 :]
        panel_lines.append(f"║ {line_str.ljust(width - 4)} ║")

    panel_lines.append(border_bottom)
    return "\n".join(panel_lines)
