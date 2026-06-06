"use client";
import React from "react";
import styles from "./SuggestedPrompts.module.css";

interface Props {
  prompts: string[];
  onSelect: (prompt: string) => void;
}

export function SuggestedPrompts({ prompts, onSelect }: Props) {
  return (
    <div className={styles.container}>
      {prompts.map((p, i) => (
        <button key={i} className={styles.chip} onClick={() => onSelect(p)}>
          {p}
        </button>
      ))}
    </div>
  );
}
