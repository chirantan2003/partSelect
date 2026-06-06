"use client";
import React, { useState, useEffect } from "react";
import { Chat } from "./components/Chat";

export default function Home() {
  const [sessionId, setSessionId] = useState<string | null>(null);

  useEffect(() => {
    // Generate simple persistent session ID on client side
    let id = localStorage.getItem("ps_session_id");
    if (!id) {
      id = "session_" + Math.random().toString(36).substring(2, 11);
      localStorage.setItem("ps_session_id", id);
    }
    setSessionId(id);
  }, []);

  if (!sessionId) {
    return (
      <div style={{
        display: 'flex',
        justifyContent: 'center',
        alignItems: 'center',
        height: '100vh',
        background: 'var(--ps-bg)',
        color: 'var(--ps-muted)',
        fontFamily: 'var(--font)'
      }}>
        Loading Session...
      </div>
    );
  }

  return <Chat sessionId={sessionId} />;
}
