import { useEffect, useRef } from "react";
import type { ChatMessage } from "../api/client";
import MessageBubble from "./MessageBubble";
import ChatInput from "./ChatInput";
import PromptSuggestions from "./PromptSuggestions";

interface ChatPanelProps {
  messages: ChatMessage[];
  isStreaming: boolean;
  onSend: (text: string) => void;
}

export default function ChatPanel({ messages, isStreaming, onSend }: ChatPanelProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  return (
    <div className="flex flex-1 flex-col">
      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-4 py-6 bg-slate-50 dark:bg-gray-950">
        {messages.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center">
            <div className="text-center">
              <div className="flex items-center justify-center gap-3">
                <svg
                  viewBox="0 0 128 128"
                  className="h-10 w-10 rounded-xl bg-blue-100 p-1.5 text-blue-600 shadow-sm ring-1 ring-blue-200/60 dark:bg-gray-800 dark:text-blue-400 dark:ring-gray-700"
                  aria-hidden="true"
                >
                  <g transform="translate(14, 16) scale(0.1, -0.1)">
                    <path
                      fill="currentColor"
                      d="M190-120q-13 0-21.5-8.5T160-150v-50h-40v-150q0-13 8.5-21.5T150-380h50v-310q0-64 47-107t113-43q63 0 106.5 43.5T510-690v420q0 38 26 64t64 26q41 0 70.5-25.5T700-270v-310h-50q-13 0-21.5-8.5T620-610v-150h40v-50q0-13 8.5-21.5T690-840h80q13 0 21.5 8.5T800-810v50h40v150q0 13-8.5 21.5T810-580h-50v310q0 64-47 107t-113 43q-63 0-106.5-43.5T450-270v-420q0-38-26-64t-64-26q-41 0-70.5 25.5T260-690v310h50q13 0 21.5 8.5T340-350v150h-40v50q0 13-8.5 21.5T270-120h-80Z"
                    />
                  </g>
                </svg>
                <h2 className="text-2xl font-semibold text-gray-600 dark:text-gray-500">
                  Fibey Agent
                </h2>
              </div>
              <p className="mt-2 text-sm text-gray-500 dark:text-gray-600">
                Ask me anything — I'll use the Foundry Toolbox to help.
              </p>
            </div>
            <div className="mt-8 w-full max-w-2xl">
              <PromptSuggestions onSelect={onSend} />
            </div>
          </div>
        ) : (
          <div className="mx-auto max-w-3xl space-y-4">
            {messages
              .filter((msg) => msg.role === "user" || msg.content)
              .map((msg) => (
              <MessageBubble key={msg.id} message={msg} isStreaming={isStreaming && msg === messages[messages.length - 1]} />
            ))}
            {isStreaming && (
              <div className="flex items-center gap-2 text-sm text-gray-400">
                <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-blue-500" />
                Agent working…
              </div>
            )}
            <div ref={bottomRef} />
          </div>
        )}
      </div>

      {/* Input */}
      <ChatInput onSend={onSend} disabled={isStreaming} />
    </div>
  );
}
