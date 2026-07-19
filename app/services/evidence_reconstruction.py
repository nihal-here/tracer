from typing import Iterable, Sequence, Any
from collections import defaultdict

class GapInCoverageError(Exception):
    pass

def reconstruct_evidence_text(excerpts: Sequence[Any], spans: Sequence[Any]) -> str:
    """
    Reconstructs exact lines requested by excerpts using the raw text available in spans.
    Deduplicates overlapping lines and preserves source order.
    Rejects any gap in coverage.
    """
    if not excerpts:
        return ""

    # 1. Index available lines by path
    # For each path, we maintain a dictionary of {line_number: text_of_line}
    available_lines: dict[str, dict[int, str]] = defaultdict(dict)
    for span in spans:
        # Avoid splitlines() here so we don't accidentally drop trailing newlines in weird ways,
        # but split('\n') is simple and correct if we are just tracking line by line.
        lines = span.content.split('\n')
        # If the last element is empty because of a trailing newline, don't map it to a line number
        if lines and lines[-1] == "":
            lines = lines[:-1]

        for i, line_text in enumerate(lines):
            line_num = span.start_line + i
            if line_num <= span.end_line:
                available_lines[span.path][line_num] = line_text

    # 2. Gather requested lines by path
    requested_lines: dict[str, set[int]] = defaultdict(set)
    justifications: dict[str, set[str]] = defaultdict(set)

    for excerpt in excerpts:
        for line_num in range(excerpt.start_line, excerpt.end_line + 1):
            requested_lines[excerpt.path].add(line_num)
        if excerpt.justification:
            justifications[excerpt.path].add(excerpt.justification)

    # 3. Check for gaps and reconstruct
    reconstructed_blocks = []

    # Sort paths for stable output
    for path in sorted(requested_lines.keys()):
        req_set = requested_lines[path]
        avail_dict = available_lines[path]

        # Check gaps
        missing = req_set - set(avail_dict.keys())
        if missing:
            min_miss = min(missing)
            max_miss = max(missing)
            raise GapInCoverageError(f"Coverage gap for {path}: lines {min_miss}-{max_miss} were not observed.")

        # Group into contiguous blocks
        sorted_req = sorted(list(req_set))
        if not sorted_req:
            continue

        blocks = []
        current_block = [sorted_req[0]]

        for i in range(1, len(sorted_req)):
            if sorted_req[i] == current_block[-1] + 1:
                current_block.append(sorted_req[i])
            else:
                blocks.append(current_block)
                current_block = [sorted_req[i]]
        blocks.append(current_block)

        # Format output
        path_output = f"--- {path} ---\n"
        if justifications[path]:
            path_output += f"Justifications: {'; '.join(sorted(list(justifications[path])))}\n"

        for block in blocks:
            path_output += f"Lines {block[0]}-{block[-1]}:\n"
            for line_num in block:
                path_output += avail_dict[line_num] + "\n"

        reconstructed_blocks.append(path_output)

    return "\n".join(reconstructed_blocks)
