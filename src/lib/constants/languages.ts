export const LANGUAGE_VALUES = [
  "auto",
  "cpp",
  "java",
  "javascript",
  "python",
  "go",
  "rust",
  "csharp",
  "kotlin",
  "plaintext",
] as const;

export type CodeLanguageValue = (typeof LANGUAGE_VALUES)[number];

export const CODE_LANGUAGES: { value: CodeLanguageValue; label: string }[] = [
  { value: "auto", label: "Detectar automaticamente" },
  { value: "cpp", label: "C/C++" },
  { value: "java", label: "Java" },
  { value: "javascript", label: "JavaScript" },
  { value: "python", label: "Python" },
  { value: "go", label: "Go" },
  { value: "rust", label: "Rust" },
  { value: "csharp", label: "C#" },
  { value: "kotlin", label: "Kotlin" },
  { value: "plaintext", label: "Texto" },
];

export function detectLanguageFromContent(content: string): CodeLanguageValue {
  const snippet = content.slice(0, 200).toLowerCase();
  if (snippet.includes("#include") || snippet.includes("std::")) return "cpp";
  if (/def\s+\w+\(/.test(snippet) || snippet.includes("import sys")) return "python";
  if (snippet.includes("console.log") || snippet.includes("function ")) return "javascript";
  if (snippet.includes("package main") || snippet.includes("fmt.")) return "go";
  if (snippet.includes("public class") || snippet.includes("system.out")) return "java";
  if (snippet.includes("fn main") || snippet.includes("println!")) return "rust";
  if (snippet.includes("using system") || snippet.includes("Console.WriteLine")) return "csharp";
  return "plaintext";
}
