import { type NextRequest } from 'next/server';

export const dynamic = 'force-dynamic';

export async function proxyToSqlAdmin(request: NextRequest): Promise<Response> {
  const botRunnerBase = (process.env.BOT_RUNNER_URL || 'http://localhost:7860').replace(/\/$/, '');
  // Strip the public /db prefix; SQLAdmin is still mounted at /console/db on the agent-runner.
  const subPath = request.nextUrl.pathname.replace(/^\/db/, '') || '/';
  const targetUrl = `${botRunnerBase}/console/db${subPath}${request.nextUrl.search}`;

  const forwardHeaders = new Headers();
  for (const [key, val] of request.headers.entries()) {
    if (key === 'host' || key.startsWith('x-nextjs-') || key === 'next-url') continue;
    forwardHeaders.set(key, val);
  }
  // Authenticate with agent-runner so direct access to port 7860 is blocked
  if (process.env.BOT_RUNNER_SECRET) {
    forwardHeaders.set('Authorization', `Bearer ${process.env.BOT_RUNNER_SECRET}`);
  }

  const hasBody = request.method !== 'GET' && request.method !== 'HEAD';

  const upstream = await fetch(targetUrl, {
    method: request.method,
    headers: forwardHeaders,
    body: hasBody ? request.body : null,
    redirect: 'manual',
    ...(hasBody ? { duplex: 'half' } : {}),
  } as RequestInit);

  const publicOrigin = `${request.nextUrl.protocol}//${request.headers.get('host') ?? 'localhost:3000'}`;
  const resHeaders = new Headers();

  for (const [key, val] of upstream.headers.entries()) {
    if (key === 'location') {
      // Rewrite absolute redirects pointing at the bot runner back to the public origin,
      // then remap the internal /console/db mount point to the public /db path.
      let loc = val.startsWith(botRunnerBase) ? publicOrigin + val.slice(botRunnerBase.length) : val;
      loc = loc.replace('/console/db', '/db');
      resHeaders.set(key, loc);
    } else if (key === 'content-length') {
      // Omit — may be wrong after HTML body rewrite below.
    } else {
      resHeaders.set(key, val);
    }
  }

  // SQLAdmin embeds absolute URLs (via Starlette url_for) using the upstream server address.
  // Rewrite them in HTML responses so all asset/link hrefs point through our proxy.
  const contentType = resHeaders.get('content-type') ?? '';
  if (contentType.includes('text/html')) {
    const text = await upstream.text();
    const rewritten = text
      .replaceAll(botRunnerBase, publicOrigin)
      .replaceAll('/console/db', '/db')
      .replace(
        '<body>',
        `<body><div style="position:fixed;top:0;left:0;right:0;z-index:9999;background:#1e293b;padding:6px 16px;font-family:sans-serif;font-size:13px;"><a href="/" style="color:#94a3b8;text-decoration:none;">← Console</a></div><div style="height:36px;"></div>`
      );
    return new Response(rewritten, { status: upstream.status, headers: resHeaders });
  }

  return new Response(upstream.body, { status: upstream.status, headers: resHeaders });
}
