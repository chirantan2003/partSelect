"use client";
import { useState, useCallback, useRef } from "react";

export interface ToolResult {
  tool_name: string;
  args: Record<string, any>;
  result: Record<string, any>;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  toolResults?: ToolResult[];
}

export function useAgentChat({ sessionId }: { sessionId: string }) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [status, setStatus] = useState<"idle" | "streaming">("idle");
  const [toolStatus, setToolStatus] = useState<string | null>(null);
  const idRef = useRef(0);

  const sendMessage = useCallback(async (text: string) => {
    if (!text.trim()) return;

    // Create user message
    const userMsg: ChatMessage = {
      id: `msg-${++idRef.current}`,
      role: "user",
      content: text,
    };

    const assistantId = `msg-${++idRef.current}`;
    
    // Compute current messages synchronously
    const backendMessages = [...messages, userMsg];

    // We add the user message and a placeholder for the assistant
    setMessages(prev => [
      ...prev,
      userMsg,
      { id: assistantId, role: "assistant", content: "", toolResults: [] }
    ]);
    
    setStatus("streaming");
    setToolStatus(null);

    let assistantText = "";
    const toolResults: ToolResult[] = [];

    try {
      const resp = await fetch("http://localhost:8000/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          messages: backendMessages.map(m => ({ role: m.role, content: m.content })),
          session_id: sessionId,
        }),
      });

      if (!resp.body) {
        throw new Error("No response body");
      }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        const blocks = buffer.split("\n\n");
        buffer = blocks.pop() || "";

        for (const block of blocks) {
          if (!block.trim()) continue;

          const lines = block.split("\n");
          let eventType = "";
          let dataStr = "";

          for (const line of lines) {
            if (line.startsWith("event: ")) {
              eventType = line.slice(7).trim();
            } else if (line.startsWith("data: ")) {
              dataStr = line.slice(6).trim();
            }
          }

          if (!eventType || !dataStr) continue;

          let data;
          try {
            data = JSON.parse(dataStr);
          } catch {
            data = dataStr;
          }

          if (eventType === "tool_status") {
            setToolStatus(data.message || data);
          } else if (eventType === "tool_result") {
            toolResults.push(data);
            setMessages(prev => {
              return prev.map(m => {
                if (m.id === assistantId) {
                  return {
                    ...m,
                    toolResults: [...toolResults]
                  };
                }
                return m;
              });
            });
          } else if (eventType === "text") {
            assistantText = data;
            setMessages(prev => {
              return prev.map(m => {
                if (m.id === assistantId) {
                  return {
                    ...m,
                    content: assistantText
                  };
                }
                return m;
              });
            });
          } else if (eventType === "done") {
            // Stream finished successfully
          }
        }
      }
    } catch (err) {
      console.error("SSE stream error:", err);
      setMessages(prev => {
        return prev.map(m => {
          if (m.id === assistantId) {
            return {
              ...m,
              content: "Sorry, I encountered an error. Please check your connection and try again."
            };
          }
          return m;
        });
      });
    } finally {
      setStatus("idle");
      setToolStatus(null);
    }
  }, [sessionId]);

  return { messages, sendMessage, status, toolStatus };
}
