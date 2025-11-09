import { NextRequest } from "next/server";

import { successResponse } from "@/app/api/_lib/responses";
import { fetchGuideCatalog } from "@/lib/usaco/guide";

export async function GET(req: NextRequest) {
  const url = new URL(req.url);
  const divisionFilter = url.searchParams.get("division");
  const search = url.searchParams.get("search");

  const catalog = await fetchGuideCatalog();
  let modules = catalog.modules;

  if (divisionFilter) {
    const normalized = divisionFilter.toLowerCase();
    modules = modules.filter((module) => module.division.toLowerCase() === normalized);
  }

  if (search) {
    const term = search.trim().toLowerCase();
    modules = modules.filter(
      (module) =>
        module.title.toLowerCase().includes(term) ||
        module.guideModuleId.toLowerCase().includes(term),
    );
  }

  return successResponse(modules);
}
