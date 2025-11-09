import { Octokit } from "octokit";
import { z } from "zod";

const ProblemMetaSchema = z.object({
  uniqueId: z.string(),
  name: z.string(),
  url: z.string().min(1),
  source: z.string().optional().nullable(),
  difficulty: z.string().optional().nullable(),
  tags: z.array(z.string()).optional().nullable(),
});

export type GuideModule = {
  id: string;
  guideModuleId: string;
  title: string;
  division: string;
  orderIndex: number;
  url: string;
  guideVersion: string;
};

export type GuideProblem = {
  id: string;
  uniqueId: string;
  name: string;
  url: string;
  source: string | null;
  difficulty: string | null;
  tags: string[];
  guideModuleId: string;
};

export type GuideCatalog = {
  commitSha: string;
  modules: GuideModule[];
  problems: GuideProblem[];
};

const CACHE_TTL_MS = 1000 * 60 * 60; // 1 hour
let cachedCatalog: { fetchedAt: number; data: GuideCatalog } | null = null;

export async function fetchGuideCatalog(options?: { force?: boolean }) {
  const now = Date.now();
  if (!options?.force && cachedCatalog && now - cachedCatalog.fetchedAt < CACHE_TTL_MS) {
    return cachedCatalog.data;
  }

  const octokit = new Octokit({ auth: process.env.GITHUB_TOKEN });
  const ref = process.env.USACO_GUIDE_REF ?? "master";
  const commit = await octokit.request("GET /repos/{owner}/{repo}/commits/{ref}", {
    owner: "cpinitiative",
    repo: "usaco-guide",
    ref,
  });

  const commitSha = commit.data.sha;
  const tree = await octokit.request("GET /repos/{owner}/{repo}/git/trees/{tree_sha}", {
    owner: "cpinitiative",
    repo: "usaco-guide",
    tree_sha: commitSha,
    recursive: "1",
  });

  const files = (tree.data.tree ?? []).filter(
    (node): node is { path: string } => Boolean(node.path?.endsWith(".problems.json")),
  );

  const modulesMap = new Map<string, GuideModule & { filePath: string }>();
  const problems: GuideProblem[] = [];
  const seenProblems = new Set<string>();

  await runWithConcurrency(files, 5, async (file) => {
    const path = file.path;
    const rawUrl = `https://raw.githubusercontent.com/cpinitiative/usaco-guide/${commitSha}/${path}`;
    const response = await fetch(rawUrl);
    if (!response.ok) return;

    let parsed: unknown;
    try {
      parsed = JSON.parse(await response.text());
    } catch (error) {
      console.warn("Failed to parse", path, error);
      return;
    }
    const moduleRecord = parsed as Record<string, unknown>;
    const moduleIdValue = moduleRecord["MODULE_ID"];
    const divisionValue = moduleRecord["division"];
    const titleValue = moduleRecord["title"];
    const moduleId = normalizeModuleId(
      typeof moduleIdValue === "string"
        ? moduleIdValue
        : inferModuleIdFromPath(path),
    );
    const division =
      typeof divisionValue === "string"
        ? divisionValue
        : inferDivisionFromPath(path);
    const title =
      typeof titleValue === "string"
        ? titleValue
        : inferTitleFromPath(path);

    if (!modulesMap.has(moduleId)) {
      modulesMap.set(moduleId, {
        id: moduleId,
        guideModuleId: moduleId,
        title,
        division,
        orderIndex: 0,
        url: `https://usaco.guide/${moduleId}`,
        guideVersion: commitSha,
        filePath: path,
      });
    }

    const candidates = extractProblemCandidates(parsed);
    for (const candidate of candidates) {
      const result = ProblemMetaSchema.safeParse(candidate);
      if (!result.success) continue;

      const problem = result.data;
      const dedupeKey = `${moduleId}:${problem.uniqueId}`;
      if (seenProblems.has(dedupeKey)) continue;
      seenProblems.add(dedupeKey);

      const normalizedUrl = normalizeUrl(problem.url);
      if (!normalizedUrl) continue;

      problems.push({
        id: problem.uniqueId,
        uniqueId: problem.uniqueId,
        name: problem.name,
        url: normalizedUrl,
        source: problem.source ?? null,
        difficulty: problem.difficulty ?? null,
        tags: problem.tags?.filter(Boolean) ?? [],
        guideModuleId: moduleId,
      });
    }
  });

  const modules = Array.from(modulesMap.values())
    .sort((a, b) => {
      const divisionOrder = getDivisionOrder(a.division) - getDivisionOrder(b.division);
      if (divisionOrder !== 0) return divisionOrder;
      return a.filePath.localeCompare(b.filePath);
    })
    .map((module, index) => ({
      id: module.id,
      guideModuleId: module.guideModuleId,
      title: module.title,
      division: module.division,
      orderIndex: index + 1,
      url: module.url,
      guideVersion: commitSha,
    }));

  const moduleOrder = new Map(modules.map((module, index) => [module.guideModuleId, index]));
  const catalog: GuideCatalog = {
    commitSha,
    modules,
    problems: problems.sort((a, b) => {
      const orderDiff = (moduleOrder.get(a.guideModuleId) ?? 0) - (moduleOrder.get(b.guideModuleId) ?? 0);
      if (orderDiff !== 0) return orderDiff;
      return a.name.localeCompare(b.name);
    }),
  };

  cachedCatalog = { fetchedAt: now, data: catalog };
  return catalog;
}

function normalizeModuleId(value: string) {
  return value.trim().replace(/\s+/g, "-").toLowerCase();
}

function inferModuleIdFromPath(path: string) {
  return path
    .replace(/^content\//, "")
    .replace(/\.problems\.json$/, "")
    .replace(/\//g, "-")
    .replace(/_/g, "-")
    .toLowerCase();
}

function inferDivisionFromPath(path: string) {
  const segment = path.replace(/^content\//, "").split("/")[0] ?? "1_General";
  if (segment.includes("Bronze")) return "Bronze";
  if (segment.includes("Silver")) return "Silver";
  if (segment.includes("Gold")) return "Gold";
  if (segment.includes("Platinum")) return "Platinum";
  if (segment.includes("Advanced")) return "Advanced";
  return "General";
}

function inferTitleFromPath(path: string) {
  const fileName = path.split("/").pop() ?? "";
  const baseName = fileName.replace(/\.problems\.json$/, "");
  return baseName
    .replace(/_/g, " ")
    .replace(/-/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/\w\S*/g, (txt) => txt.charAt(0).toUpperCase() + txt.slice(1).toLowerCase());
}

function normalizeUrl(raw: string) {
  if (!raw) return null;
  try {
    const url = new URL(raw);
    return url.toString();
  } catch {
    try {
      const url = new URL(`https://${raw}`);
      return url.toString();
    } catch (err) {
      console.warn("Invalid problem URL", raw, err);
      return null;
    }
  }
}

function extractProblemCandidates(payload: unknown): unknown[] {
  if (!payload || typeof payload !== "object") return [];
  const values: unknown[] = [];
  for (const value of Object.values(payload as Record<string, unknown>)) {
    if (!value) continue;
    if (Array.isArray(value)) {
      values.push(...value);
    } else if (typeof value === "object") {
      values.push(...extractProblemCandidates(value));
    }
  }
  return values;
}

function getDivisionOrder(division: string) {
  const order = ["General", "Bronze", "Silver", "Gold", "Platinum", "Advanced"];
  const idx = order.indexOf(division);
  return idx === -1 ? order.length : idx;
}

async function runWithConcurrency<T>(items: T[], limit: number, task: (item: T, index: number) => Promise<void>) {
  if (items.length === 0) return;
  const concurrency = Math.min(limit, items.length);
  let currentIndex = 0;

  await Promise.all(
    Array.from({ length: concurrency }).map(async () => {
      while (currentIndex < items.length) {
        const index = currentIndex++;
        await task(items[index]!, index);
      }
    }),
  );
}
