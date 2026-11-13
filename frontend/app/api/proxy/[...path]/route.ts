import { NextRequest, NextResponse } from "next/server";

import { isProxied } from "@/lib/proxyAllowlist";

export const dynamic = "force-dynamic";

const ENGINE_URL = (process.env.FIE_ENGINE_URL || "http://localhost:8001").replace(
  /\/+$/,
  "",
);

async function forward(request: NextRequest, segments: string[]) {
  const path = `/${segments.join("/")}`;
  if (!isProxied(request.method, path)) {
    return NextResponse.json({ detail: "Not found" }, { status: 404 });
  }

  const headers: Record<string, string> = { Accept: "application/json" };
  const contentType = request.headers.get("content-type");
  if (contentType) headers["Content-Type"] = contentType;

  // Server-only: the dashboard never sees this value.
  const key = process.env.FIE_API_KEY;
  if (key) headers["X-API-Key"] = key;

  const body = request.method === "GET" ? undefined : await request.text();

  let upstream: Response;
  try {
    upstream = await fetch(`${ENGINE_URL}${path}${request.nextUrl.search}`, {
      method: request.method,
      headers,
      body: body ? body : undefined,
      cache: "no-store",
    });
  } catch {
    return NextResponse.json(
      { detail: `Cannot reach the engine at ${ENGINE_URL}` },
      { status: 502 },
    );
  }

  return new NextResponse(await upstream.arrayBuffer(), {
    status: upstream.status,
    headers: {
      "Content-Type": upstream.headers.get("content-type") || "application/json",
    },
  });
}

type Context = { params: { path: string[] } };

export async function GET(request: NextRequest, { params }: Context) {
  return forward(request, params.path);
}

export async function POST(request: NextRequest, { params }: Context) {
  return forward(request, params.path);
}

export async function PUT(request: NextRequest, { params }: Context) {
  return forward(request, params.path);
}
