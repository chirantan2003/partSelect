"use client";
import React, { useState, useRef, useEffect } from "react";
import styles from "./Composer.module.css";

interface Props {
  onSend: (text: string) => void;
  disabled: boolean;
}

export function Composer({ onSend, disabled }: Props) {
  const [text, setText] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!text.trim() || disabled) return;
    onSend(text.trim());
    setText("");
  };

  useEffect(() => {
    if (!disabled && inputRef.current) {
      inputRef.current.focus();
    }
  }, [disabled]);

  return (
    <form onSubmit={handleSubmit} className={styles.form}>
      <input
        ref={inputRef}
        type="text"
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="Ask about part lookups, compatibility, or repair guides..."
        className={styles.input}
        disabled={disabled}
      />
      <button type="submit" className={styles.button} disabled={disabled || !text.trim()}>
        Send
      </button>
    </form>
  );
}
