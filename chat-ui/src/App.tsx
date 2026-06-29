import { useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { streamChat } from './api/chatClient';

interface Message {
  role: 'user' | 'assistant';
  text: string;
  sources?: string[];
  streaming?: boolean;
  error?: boolean;
}

const SUGGESTIONS = [
  '住宅ローンの金利を教えて',
  '外貨預金の手数料は？',
  '口座開設の方法を知りたい',
];

export function App() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const sessionId = useRef<string>(crypto.randomUUID());
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, [messages]);

  async function send(text: string) {
    const message = text.trim();
    if (!message || busy) return;
    setInput('');
    setBusy(true);
    setMessages((m) => [
      ...m,
      { role: 'user', text: message },
      { role: 'assistant', text: '', streaming: true },
    ]);

    const patchLast = (fn: (m: Message) => Message) =>
      setMessages((all) => {
        const next = [...all];
        next[next.length - 1] = fn(next[next.length - 1]);
        return next;
      });

    await streamChat(message, sessionId.current, {
      onSources: (urls) => patchLast((m) => ({ ...m, sources: urls })),
      onToken: (t) => patchLast((m) => ({ ...m, text: m.text + t })),
      onDone: () => patchLast((m) => ({ ...m, streaming: false })),
      onError: (msg) =>
        patchLast((m) => ({ ...m, text: m.text || msg, streaming: false, error: true })),
    });
    setBusy(false);
  }

  return (
    <div className="app">
      <header className="header">
        <div className="brand">au じぶん銀行</div>
        <div className="subtitle">AI アシスタント（デモ）</div>
      </header>

      <div className="messages" ref={scrollRef}>
        {messages.length === 0 && (
          <div className="empty">
            <p>ご質問をどうぞ。よくある質問の例：</p>
            <div className="suggestions">
              {SUGGESTIONS.map((s) => (
                <button key={s} className="chip" onClick={() => send(s)} disabled={busy}>
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((m, i) => (
          <div key={i} className={`row ${m.role}`}>
            <div className={`bubble ${m.role} ${m.error ? 'error' : ''}`}>
              {m.text ? (
                m.role === 'assistant' && !m.error ? (
                  <div className="markdown">
                    <ReactMarkdown>{m.text}</ReactMarkdown>
                  </div>
                ) : (
                  m.text
                )
              ) : m.streaming ? (
                <span className="dots">●●●</span>
              ) : (
                ''
              )}
              {m.role === 'assistant' && m.sources && m.sources.length > 0 && (
                <div className="sources">
                  <span className="sources-label">参照元</span>
                  <ul>
                    {m.sources.map((u) => (
                      <li key={u}>
                        <a href={u} target="_blank" rel="noreferrer">
                          {u}
                        </a>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          </div>
        ))}
      </div>

      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          send(input);
        }}
      >
        <input
          className="text-input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="メッセージを入力…"
          disabled={busy}
          aria-label="メッセージ"
        />
        <button className="send" type="submit" disabled={busy || !input.trim()}>
          送信
        </button>
      </form>
      <footer className="disclaimer">
        本デモの回答は AI が生成したものです。正確性は各公式ページでご確認ください。
      </footer>
    </div>
  );
}
