import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from django.core.management.base import BaseCommand

from core.models import NivelUSACO, ModuloTeorico, ProblemaReferencia

USACO_REPO_URL = "https://github.com/cpinitiative/usaco-guide.git"
USACO_BASE_URL = "https://usaco.guide"

SECTION_DIRS = {
    "bronze": "2_Bronze",
    "silver": "3_Silver",
    "gold": "4_Gold",
    "plat": "5_Plat",
}

SECTION_LABELS = {
    "bronze": "Bronze",
    "silver": "Silver",
    "gold": "Gold",
    "plat": "Platinum",
}

SECTION_ORDER = {
    "bronze": 1,
    "silver": 2,
    "gold": 3,
    "plat": 4,
}


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1].strip()
    return value


def _parse_frontmatter(text: str) -> dict:
    if not text.startswith("---"):
        return {}
    lines = text.splitlines()
    fm_lines = []
    for line in lines[1:]:
        if line.strip() == "---":
            break
        fm_lines.append(line.rstrip("\n"))

    data = {}
    current_key = None
    buffer = []
    for line in fm_lines:
        match = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if match:
            if current_key and buffer:
                data[current_key] = _strip_quotes(" ".join(buffer))
                buffer = []
            current_key = match.group(1)
            value = match.group(2).strip()
            if value:
                data[current_key] = _strip_quotes(value)
                current_key = None
            continue

        if current_key and (line.startswith("  ") or line.startswith("\t")):
            buffer.append(line.strip())
        elif current_key:
            current_key = None
            buffer = []

    if current_key and buffer:
        data[current_key] = _strip_quotes(" ".join(buffer))

    return data


def _parse_ordering(path: Path) -> dict:
    sections = {section: [] for section in SECTION_DIRS}
    current_section = None
    collecting = False
    buffer = ""

    for line in path.read_text(encoding="utf-8").splitlines():
        section_match = re.match(r"^\s*(general|bronze|silver|gold|plat|adv):\s*\[", line)
        if section_match:
            current_section = section_match.group(1)

        if current_section not in sections:
            continue

        if "items:" in line:
            tail = line.split("items:", 1)[1]
            if "[" in tail and "]" in tail:
                sections[current_section].extend(re.findall(r"'([^']+)'", tail))
            else:
                collecting = True
                buffer = tail
                if "]" in tail:
                    sections[current_section].extend(re.findall(r"'([^']+)'", buffer))
                    collecting = False
                    buffer = ""
            continue

        if collecting:
            buffer += " " + line
            if "]" in line:
                sections[current_section].extend(re.findall(r"'([^']+)'", buffer))
                collecting = False
                buffer = ""

    return sections


def _infer_platform(url: str) -> str:
    lowered = url.lower()
    if "codeforces.com" in lowered:
        return "CF"
    if "atcoder.jp" in lowered:
        return "AC"
    return "OJ"


def _parse_codeforces_id(url: str) -> tuple[str, str] | tuple[None, None]:
    patterns = [
        r"/contest/(\d+)/problem/([A-Za-z0-9]+)",
        r"/problemset/problem/(\d+)/([A-Za-z0-9]+)",
        r"/gym/(\d+)/problem/([A-Za-z0-9]+)",
        r"/problemset/gymProblem/(\d+)/([A-Za-z0-9]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1), match.group(2)
    return None, None


def _parse_atcoder_id(url: str) -> str | None:
    match = re.search(r"/contests/[^/]+/tasks/([^/?#]+)", url)
    if match:
        return match.group(1)
    return None


def _extract_problem_id(platform: str, url: str, unique_id: str, name: str) -> str:
    if platform == "CF":
        contest_id, index = _parse_codeforces_id(url)
        if contest_id and index:
            return f"{contest_id}{index}"
    elif platform == "AC":
        task_id = _parse_atcoder_id(url)
        if task_id:
            return task_id

    return unique_id or url or name


def _load_problems(path: Path) -> dict:
    problems_map = {}
    for problems_file in path.glob("*.problems.json"):
        data = json.loads(problems_file.read_text(encoding="utf-8"))
        module_id = data.get("MODULE_ID")
        if not module_id:
            continue

        entries = []
        for value in data.values():
            if isinstance(value, list):
                entries.extend(value)

        unique = {}
        for entry in entries:
            unique_key = entry.get("uniqueId") or entry.get("url") or entry.get("name")
            if unique_key:
                unique[unique_key] = entry

        problems_map[module_id] = list(unique.values())

    return problems_map


class Command(BaseCommand):
    help = "Sincroniza o curriculo completo do USACO Guide (open source)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--local-path",
            dest="local_path",
            help="Caminho local para o repo usaco-guide (evita clone).",
        )
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Limpa modulos e problemas dos niveis USACO antes de importar.",
        )

    def handle(self, *args, **options):
        local_path = options.get("local_path")
        reset = options.get("reset", False)

        self.stdout.write("Iniciando sincronizacao do USACO Guide...")

        temp_dir = None
        repo_path = None

        try:
            if local_path:
                repo_path = Path(local_path).resolve()
            else:
                temp_dir = tempfile.mkdtemp(prefix="usaco-guide-")
                repo_path = Path(temp_dir)
                subprocess.run(
                    ["git", "clone", "--depth", "1", USACO_REPO_URL, str(repo_path)],
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )

            ordering_path = repo_path / "content" / "ordering.ts"
            if not ordering_path.exists():
                raise FileNotFoundError("Arquivo content/ordering.ts nao encontrado.")

            ordering = _parse_ordering(ordering_path)
            module_meta = {}
            problems_by_module = {}

            for section, folder in SECTION_DIRS.items():
                section_path = repo_path / "content" / folder
                if not section_path.exists():
                    continue

                for mdx_file in section_path.glob("*.mdx"):
                    fm = _parse_frontmatter(mdx_file.read_text(encoding="utf-8"))
                    module_id = fm.get("id")
                    if not module_id:
                        continue
                    module_meta[module_id] = {
                        "title": fm.get("title") or module_id,
                        "description": fm.get("description") or "",
                        "section": section,
                    }

                problems_by_module.update(_load_problems(section_path))

            for section, label in SECTION_LABELS.items():
                level, _ = NivelUSACO.objects.update_or_create(
                    nome=label,
                    defaults={"ordem": SECTION_ORDER[section]},
                )

                if reset:
                    ModuloTeorico.objects.filter(nivel=level).delete()

                module_ids = ordering.get(section, [])
                for index, module_id in enumerate(module_ids, start=1):
                    info = module_meta.get(module_id)
                    if not info:
                        continue

                    link = f"{USACO_BASE_URL}/{section}/{module_id}"
                    modulo, _ = ModuloTeorico.objects.update_or_create(
                        nivel=level,
                        link_aula=link,
                        defaults={
                            "titulo": info["title"],
                            "descricao": info["description"],
                            "ordem": index,
                        },
                    )

                    problems = problems_by_module.get(module_id, [])
                    for problem in problems:
                        url = problem.get("url", "")
                        if not url:
                            continue

                        platform = _infer_platform(url)
                        problem_id = _extract_problem_id(
                            platform,
                            url,
                            problem.get("uniqueId", ""),
                            problem.get("name", ""),
                        )
                        if not problem_id:
                            continue

                        ProblemaReferencia.objects.update_or_create(
                            modulo=modulo,
                            problema_id=problem_id,
                            plataforma=platform,
                            defaults={
                                "titulo": problem.get("name", problem_id),
                                "link": url,
                            },
                        )

            self.stdout.write(self.style.SUCCESS("Sincronizacao do USACO Guide concluida!"))

        finally:
            if temp_dir:
                shutil.rmtree(temp_dir, ignore_errors=True)
