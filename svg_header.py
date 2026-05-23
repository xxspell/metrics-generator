def make_header_tail(title: str, target_length: int = 60) -> str:
    """Return tail part that goes after </tspan>, keeping total line length exact."""
    tail_len = max(0, target_length - len(title))
    if tail_len == 0:
        return ''
    if tail_len == 1:
        return ' '
    if tail_len == 2:
        return ' -'
    if tail_len == 3:
        return ' -—'
    if tail_len == 4:
        return ' -—-'

    # Keep visual style: space + '-' + em dashes + '-—-'
    # Total length = 1 + 1 + middle + 3 = 5 + middle
    middle_len = tail_len - 5
    return ' -' + ('—' * middle_len) + '-—-'


def make_header_line(title: str, target_length: int = 64) -> str:
    """Backward-compatible helper: full line in one string."""
    return title + make_header_tail(title, target_length)
