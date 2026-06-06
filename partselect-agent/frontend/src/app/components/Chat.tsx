"use client";
import React, { useEffect, useRef } from "react";
import { useAgentChat, ToolResult } from "../../hooks/useAgentChat";
import styles from "./Chat.module.css";
import { ProductCard } from "./ProductCard";
import { CompatVerdict } from "./CompatVerdict";
import { InstallDrawer } from "./InstallDrawer";
import { SuggestedPrompts } from "./SuggestedPrompts";
import { Composer } from "./Composer";

const SUGGESTIONS = [
  "How can I install part number PS11752778?",
  "Is PS11752778 compatible with WDT780SAEM1?",
  "The ice maker on my Whirlpool fridge is not working. How can I fix it?",
  "Is PS11752778 compatible with WRS322FDAM00?"
];

export function Chat({ sessionId }: { sessionId: string }) {
  const { messages, sendMessage, status, toolStatus } = useAgentChat({ sessionId });
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, toolStatus, status]);

  return (
    <div className={styles.container}>
      <header className={styles.header}>
        <div className={styles.brand}>
          <span className={styles.brandPart}>Part</span>
          <span className={styles.brandSelect}>Select</span>
        </div>
        <span className={styles.badge}>AI Assistant</span>
      </header>

      <div className={styles.messages}>
        {messages.length === 0 && (
          <div className={styles.welcome}>
            <h2 className={styles.welcomeTitle}>Welcome to PartSelect AI!</h2>
            <p className={styles.welcomeSubtitle}>
              I can help you troubleshoot appliance issues, lookup parts, check compatibility,
              and guide you through step-by-step installation instructions.
            </p>
            <SuggestedPrompts prompts={SUGGESTIONS} onSelect={sendMessage} />
          </div>
        )}

        {messages.map((m) => {
          if (m.role === "user") {
            return (
              <div key={m.id} className={styles.userRow}>
                <div className={styles.userBubble}>{m.content}</div>
              </div>
            );
          } else {
            const hasTools = m.toolResults && m.toolResults.length > 0;
            const hasText = m.content && m.content.trim().length > 0;
            
            if (!hasTools && !hasText) return null;

            return (
              <div key={m.id} className={styles.assistantRow}>
                <div className={styles.avatar}>AI</div>
                <div className={styles.assistantContent}>
                  {hasTools && (
                    <div className={styles.toolsBlock}>
                      {m.toolResults?.map((tr, idx) => (
                        <ToolResultCard key={idx} result={tr} />
                      ))}
                    </div>
                  )}
                  {hasText && (
                    <div className={styles.assistantBubble}>
                      {m.content}
                    </div>
                  )}
                </div>
              </div>
            );
          }
        })}

        {toolStatus && (
          <div className={styles.thinkingRow}>
            <div className={styles.thinkingAvatar}>⚙️</div>
            <div className={styles.thinkingText}>{toolStatus}</div>
          </div>
        )}

        {status === "streaming" && !toolStatus && (
          <div className={styles.typingRow}>
            <div className={styles.avatar}>AI</div>
            <div className={styles.typingBubble}>
              <span className={styles.dot}></span>
              <span className={styles.dot}></span>
              <span className={styles.dot}></span>
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      <div className={styles.inputArea}>
        <Composer onSend={sendMessage} disabled={status === "streaming"} />
      </div>
    </div>
  );
}

function ToolResultCard({ result }: { result: ToolResult }) {
  const { tool_name, result: data } = result;
  
  if (tool_name === "lookup_part" && !data.error) {
    return <ProductCard data={data} />;
  }
  if (tool_name === "check_compatibility") {
    return <CompatVerdict data={data} />;
  }
  if (tool_name === "get_installation_info" && !data.error) {
    return <InstallDrawer data={data} />;
  }
  return null;
}
