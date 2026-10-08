import re

_REF = re.compile(r"\$\{([^}]*)\}")


def parse(text: str) -> dict[str, dict[str, str]]:
    data: dict[str, dict[str, str]] = {}
    section, last_key = "DEFAULT", None
    for lineno, raw in enumerate(text.splitlines(), 1):
        stripped = raw.strip()
        if not stripped:
            last_key = None
            continue
        if stripped[0] in ";#":
            continue
        if raw[0].isspace() and last_key is not None:
            data[section][last_key] += "\n" + stripped
            continue
        if stripped.startswith("["):
            if not stripped.endswith("]"):
                raise ValueError(f"line {lineno}: unterminated section header")
            section = stripped[1:-1].strip()
            data.setdefault(section, {})
            last_key = None
            continue
        m = re.search(r"[=:]", stripped)
        if not m:
            raise ValueError(f"line {lineno}: expected key = value")
        key = stripped[: m.start()].strip().lower()
        if not key:
            raise ValueError(f"line {lineno}: empty key")
        data.setdefault(section, {})[key] = stripped[m.end():].strip()
        last_key = key

    def resolve(sec, key, stack):
        if (sec, key) in stack:
            raise ValueError(f"reference cycle at {sec}:{key}")
        if key in data.get(sec, {}):
            value = data[sec][key]
        elif key in data.get("DEFAULT", {}):
            sec, value = "DEFAULT", data["DEFAULT"][key]
        else:
            raise ValueError(f"missing reference {sec}:{key}")

        def sub(m):
            ref = m.group(1)
            if ":" in ref:
                s, k = ref.split(":", 1)
                if k not in data.get(s, {}):
                    raise ValueError(f"missing reference {ref}")
                return resolve(s, k, stack | {(sec, key)})
            return resolve(sec, ref, stack | {(sec, key)})

        return _REF.sub(sub, value)

    return {s: {k: resolve(s, k, frozenset()) for k in kv} for s, kv in data.items()}
