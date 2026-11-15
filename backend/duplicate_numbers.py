# Common visual substitutions in invoice identifiers.
CONFUSABLES = {frozenset(pair) for pair in ("0O", "1I", "1L", "2Z", "5S", "8B")}


def likely_typo(left: str, right: str) -> bool:
    if min(len(left), len(right)) < 5 or left == right:
        return False
    if len(left) == len(right):
        differences = [i for i, (a, b) in enumerate(zip(left, right)) if a != b]
        if len(differences) == 1:
            i = differences[0]
            return frozenset((left[i], right[i])) in CONFUSABLES
        if len(differences) == 2:
            i, j = differences
            return j == i + 1 and left[i] == right[j] and left[j] == right[i]
        return False
    if len(left) > len(right):
        left, right = right, left
    if len(right) != len(left) + 1:
        return False
    # A missing or repeated character can be a typo.
    return any(right[:i] + right[i + 1:] == left for i in range(len(right)))
