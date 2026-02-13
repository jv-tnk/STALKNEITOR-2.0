LANGUAGE_CHOICES = [
    ("cpp", "C++17/20"),
    ("c", "C"),
    ("py", "Python"),
    ("java", "Java"),
    ("js", "JavaScript"),
    ("ts", "TypeScript"),
    ("go", "Go"),
    ("rust", "Rust"),
    ("kt", "Kotlin"),
    ("cs", "C#"),
]

HLJS_CLASS = {
    "cpp": "language-cpp",
    "c": "language-c",
    "py": "language-python",
    "java": "language-java",
    "js": "language-javascript",
    "ts": "language-typescript",
    "go": "language-go",
    "rust": "language-rust",
    "kt": "language-kotlin",
    "cs": "language-csharp",
}


def get_language_options():
    return [
        {"slug": slug, "label": label, "hljs_class": HLJS_CLASS.get(slug, "language-plaintext")}
        for slug, label in LANGUAGE_CHOICES
    ]


def get_hljs_class(lang: str | None) -> str:
    if not lang:
        return "language-plaintext"
    return HLJS_CLASS.get(lang, "language-plaintext")
