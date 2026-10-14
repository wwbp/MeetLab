import { getServerConfig, requireEnv } from '@/lib/config/server';

/**
 * Shared bot-runner /start client — used by the concierge bots route (admin "Start
 * Bot") and the public start-link provisioning route. Extracted verbatim from
 * app/api/concierge/rooms/[roomName]/bots/route.ts.
 */

type BotRunnerResponse = {
  session_id?: string;
  bot_identity?: string;
  message?: string;
  error?: string;
  already_running?: boolean; // the room already had a running session; this is it
};

function toBotRunnerStartUrl(botRunnerUrl: string): string {
  const normalized = botRunnerUrl.endsWith('/') ? botRunnerUrl : `${botRunnerUrl}/`;
  return `${normalized}start`;
}

function roomSlug(roomName: string): string {
  const slug = roomName
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
  return slug.slice(0, 24) || 'room';
}

export function createBotIdentity(roomName: string): string {
  const suffix =
    typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
      ? crypto.randomUUID().replace(/-/g, '').slice(0, 10)
      : `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
  return `bot_${roomSlug(roomName)}_${suffix}`;
}

export async function callBotRunnerStart(
  roomName: string,
  botIdentity: string,
  agentName?: string,
  customData?: Record<string, string>
): Promise<{
  ok: boolean;
  status: number;
  payload?: BotRunnerResponse;
  errorText?: string;
}> {
  const config = getServerConfig();
  const botRunnerUrl = requireEnv(config.botRunnerUrl, 'BOT_RUNNER_URL');
  const endpoint = toBotRunnerStartUrl(botRunnerUrl);
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 10000);

  try {
    const body: {
      room_name: string;
      bot_identity: string;
      custom_data: Record<string, string>;
      room_config?: { agents: Array<{ agent_name: string }> };
    } = {
      room_name: roomName,
      bot_identity: botIdentity,
      // The runner persists custom_data on the Conversation row (queryable later).
      custom_data: {
        requested_by: 'concierge',
        ...customData,
      },
    };
    if (agentName) {
      body.room_config = {
        agents: [{ agent_name: agentName }],
      };
    }

    const runnerHeaders: Record<string, string> = { 'Content-Type': 'application/json' };
    if (config.botRunnerSecret) runnerHeaders['Authorization'] = `Bearer ${config.botRunnerSecret}`;

    const response = await fetch(endpoint, {
      method: 'POST',
      headers: runnerHeaders,
      body: JSON.stringify(body),
      signal: controller.signal,
    });

    const text = await response.text();
    let payloadRaw: unknown;
    let payload: BotRunnerResponse | undefined;
    if (text) {
      try {
        payloadRaw = JSON.parse(text);
        if (typeof payloadRaw === 'object' && payloadRaw !== null && !Array.isArray(payloadRaw)) {
          payload = payloadRaw as BotRunnerResponse;
        }
      } catch {
        payloadRaw = undefined;
      }
    }

    const tupleError =
      Array.isArray(payloadRaw) &&
      payloadRaw.length === 2 &&
      typeof payloadRaw[1] === 'number' &&
      payloadRaw[1] >= 400
        ? payloadRaw
        : undefined;
    const tupleErrorMessage =
      tupleError &&
      typeof tupleError[0] === 'object' &&
      tupleError[0] !== null &&
      'error' in tupleError[0] &&
      typeof (tupleError[0] as { error?: unknown }).error === 'string'
        ? (tupleError[0] as { error: string }).error
        : undefined;

    const appLevelError =
      payload?.error ??
      tupleErrorMessage ??
      (tupleError ? `Bot runner returned application error ${tupleError[1]}` : undefined);
    const isSuccess = response.ok && !appLevelError;

    return {
      ok: isSuccess,
      status: response.status,
      payload,
      errorText: isSuccess ? undefined : (appLevelError ?? text),
    };
  } catch (error) {
    return {
      ok: false,
      status: 500,
      errorText: error instanceof Error ? error.message : 'Bot runner request failed',
    };
  } finally {
    clearTimeout(timeout);
  }
}

/**
 * The console's Stop: ask the runner to stop the room's running bot at its task.
 * Removing the participant from LiveKit alone does nothing before the bot has
 * joined (5 s warm, up to 166 s cold), and the bot then joined anyway.
 */
export async function callBotRunnerStop(
  roomName: string
): Promise<{ ok: boolean; status: number; stopped?: string | null; errorText?: string }> {
  const config = getServerConfig();
  const botRunnerUrl = requireEnv(config.botRunnerUrl, 'BOT_RUNNER_URL');
  const endpoint = `${botRunnerUrl.endsWith('/') ? botRunnerUrl : `${botRunnerUrl}/`}stop`;
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (config.botRunnerSecret) headers['Authorization'] = `Bearer ${config.botRunnerSecret}`;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 10000);
  try {
    const response = await fetch(endpoint, {
      method: 'POST',
      headers,
      body: JSON.stringify({ room_name: roomName }),
      signal: controller.signal,
    });
    const text = await response.text();
    if (!response.ok) return { ok: false, status: response.status, errorText: text };
    return { ok: true, status: response.status, stopped: (JSON.parse(text) as { stopped?: string | null }).stopped };
  } catch (error) {
    return { ok: false, status: 500, errorText: error instanceof Error ? error.message : 'Bot runner request failed' };
  } finally {
    clearTimeout(timeout);
  }
}

type RoomSession = { session_id: string; bot_identity: string; started_at: string };

/**
 * The room's running session (iteration 9): which bot a room has comes from the
 * runner's session row, not from meet's memory. Throws when the runner can't
 * answer, so callers never report "no bot" for a room they couldn't check.
 */
export async function getRoomSession(roomName: string): Promise<RoomSession | null> {
  const config = getServerConfig();
  const botRunnerUrl = requireEnv(config.botRunnerUrl, 'BOT_RUNNER_URL');
  const base = botRunnerUrl.endsWith('/') ? botRunnerUrl : `${botRunnerUrl}/`;
  const headers: Record<string, string> = {};
  if (config.botRunnerSecret) headers['Authorization'] = `Bearer ${config.botRunnerSecret}`;
  const response = await fetch(`${base}rooms/${encodeURIComponent(roomName)}/session`, { headers, cache: 'no-store' });
  if (!response.ok) throw new Error(`bot runner returned ${response.status} for the room's session`);
  return ((await response.json()) as { session: RoomSession | null }).session;
}
