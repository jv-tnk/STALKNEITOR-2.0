import { NextResponse } from "next/server";

export function errorResponse(status: number, message: string, details?: unknown) {
  return NextResponse.json({ error: message, details }, { status });
}

export function successResponse<T>(payload: T, init?: ResponseInit) {
  return NextResponse.json(payload, init);
}
