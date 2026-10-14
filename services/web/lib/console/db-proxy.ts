import { type NextRequest } from 'next/server';

const dynamic = 'force-dynamic';

export async function proxyToSqlAdmin(request: NextRequest): Promise<Response> {
  const botRunnerBase = (process.env.BOT_RUNNER_URL || 'http://localhost:7860').replace(/\/$/, '');
  // SQLAdmin's root is /api/db/, but Next drops trailing slashes: ask for it directly, or the
  // runner's redirect to /api/db/ and Next's back to /api/db loop.
  const path = request.nextUrl.pathname || '/';
  const subPath = path === '/api/db' ? '/api/db/' : path;
  const targetUrl = `${botRunnerBase}${subPath}${request.nextUrl.search}`;
  // The runner's own absolute links, http or https (behind the load balancer meet forwards
  // X-Forwarded-Proto: https), become same-site ones: the browser can't reach agent-runner:7860.
  const runnerOrigin = new RegExp(`https?://${new URL(botRunnerBase).host.replace(/[.]/g, '\\.')}`, 'g');

  const forwardHeaders = new Headers();
  for (const [key, val] of request.headers.entries()) {
    if (key === 'host' || key.startsWith('x-nextjs-') || key === 'next-url') continue;
    forwardHeaders.set(key, val);
  }
  // Authenticate with agent-runner so direct access to port 7860 is blocked
  if (process.env.BOT_RUNNER_SECRET) {
    forwardHeaders.set('Authorization', `Bearer ${process.env.BOT_RUNNER_SECRET}`);
  }

  // Strip Next.js RSC navigation headers before forwarding to SQLAdmin.
  for (const key of ['rsc', 'next-router-state-tree', 'next-router-prefetch', 'next-router-segment-prefetch']) {
    forwardHeaders.delete(key);
  }

  const hasBody = request.method !== 'GET' && request.method !== 'HEAD';

  let upstream: Response;
  try {
    upstream = await fetch(targetUrl, {
      method: request.method,
      headers: forwardHeaders,
      body: hasBody ? request.body : null,
      redirect: 'manual',
      ...(hasBody ? { duplex: 'half' } : {}),
    } as RequestInit);
  } catch (err) {
    const message = err instanceof Error ? err.message : 'Failed to reach agent-runner';
    return new Response(`DB Admin unavailable: ${message}`, { status: 502 });
  }

  const resHeaders = new Headers();

  for (const [key, val] of upstream.headers.entries()) {
    if (key === 'location') {
      resHeaders.set(key, val.replace(runnerOrigin, ''));
    } else if (key === 'content-length') {
      // Omit — may be wrong after HTML body rewrite below.
    } else {
      resHeaders.set(key, val);
    }
  }

  const contentType = resHeaders.get('content-type') ?? '';
  if (contentType.includes('text/html')) {
    const text = await upstream.text();
    const rewritten = text
      .replace(runnerOrigin, '')
      .replace(
        '<body>',
        `<body><div style="position:fixed;top:0;left:0;right:0;z-index:9999;background:#1e293b;padding:6px 16px;font-family:sans-serif;font-size:13px;"><a href="/" style="color:#94a3b8;text-decoration:none;">← Console</a></div><div style="height:36px;"></div>`
      );
    return new Response(rewritten, { status: upstream.status, headers: resHeaders });
  }

  return new Response(upstream.body, { status: upstream.status, headers: resHeaders });
}
