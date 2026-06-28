// SSE client for the U-08 chat-api Function URL.
//
// POSTs a message and reads the streaming Server-Sent Events response with the
// fetch + ReadableStream API, dispatching frames to callbacks:
//   event: sources -> string[]   (emitted once, before any tokens)
//   event: token   -> string     (answer fragment; many)
//   event: done    -> { hit }    (stream complete)
//   event: error   -> message

export interface ChatCallbacks {
  onSources?: (urls: string[]) => void;
  onToken?: (text: string) => void;
  onDone?: (info: { hit: boolean }) => void;
  onError?: (message: string) => void;
}

const ENDPOINT = (import.meta.env.VITE_CHAT_ENDPOINT ?? '').replace(/\/$/, '');
const DEMO_KEY = import.meta.env.VITE_DEMO_KEY ?? '';

export async function streamChat(
  message: string,
  sessionId: string,
  cb: ChatCallbacks,
  signal?: AbortSignal,
): Promise<void> {
  if (!ENDPOINT) {
    cb.onError?.('VITE_CHAT_ENDPOINT が設定されていません');
    return;
  }
  let res: Response;
  try {
    res = await fetch(`${ENDPOINT}/chat`, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        ...(DEMO_KEY ? { 'x-demo-key': DEMO_KEY } : {}),
      },
      body: JSON.stringify({ message, sessionId }),
      signal,
    });
  } catch (e) {
    cb.onError?.(`接続に失敗しました: ${(e as Error).message}`);
    return;
  }

  if (!res.ok || !res.body) {
    cb.onError?.(`サーバエラー (HTTP ${res.status})`);
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let sep: number;
    while ((sep = buffer.indexOf('\n\n')) !== -1) {
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      dispatch(frame, cb);
    }
  }
}

function dispatch(frame: string, cb: ChatCallbacks): void {
  let event = 'message';
  let data = '';
  for (const line of frame.split('\n')) {
    if (line.startsWith('event: ')) event = line.slice(7);
    else if (line.startsWith('data: ')) data += line.slice(6);
  }
  if (!data) return;

  let parsed: unknown;
  try {
    parsed = JSON.parse(data);
  } catch {
    return;
  }

  switch (event) {
    case 'sources':
      cb.onSources?.(parsed as string[]);
      break;
    case 'token':
      cb.onToken?.(parsed as string);
      break;
    case 'done':
      cb.onDone?.(parsed as { hit: boolean });
      break;
    case 'error': {
      const msg = (parsed as { message?: string }).message ?? 'エラーが発生しました';
      cb.onError?.(msg);
      break;
    }
  }
}
