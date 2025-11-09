import { errorResponse, successResponse } from "@/app/api/_lib/responses";
import { fetchGuideCatalog } from "@/lib/usaco/guide";

export async function GET(
  _: Request,
  { params }: { params: { guideModuleId: string } },
) {
  const catalog = await fetchGuideCatalog();
  const moduleEntry = catalog.modules.find(
    (entry) => entry.guideModuleId === params.guideModuleId,
  );

  if (!moduleEntry) {
    return errorResponse(404, "Module not found");
  }

  const moduleProblems = catalog.problems.filter(
    (problem) => problem.guideModuleId === moduleEntry.guideModuleId,
  );

  return successResponse({ module: moduleEntry, problems: moduleProblems });
}
