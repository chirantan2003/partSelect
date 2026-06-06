"use client";
import React, { useState } from "react";
import styles from "./InstallDrawer.module.css";

interface Step {
  step_no: number;
  text: string;
  difficulty?: string;
  est_minutes?: number;
  video_url?: string;
}

interface InstallData {
  ps_number: string;
  steps: Step[];
  error?: string;
}

export function InstallDrawer({ data }: { data: InstallData }) {
  const [isOpen, setIsOpen] = useState(false);

  if (data.error || !data.steps || data.steps.length === 0) {
    return null;
  }

  // Get info from first step (since difficulty & est_minutes are typically shared)
  const firstStep = data.steps[0] || {};
  const difficulty = firstStep.difficulty || "Easy";
  const estMinutes = firstStep.est_minutes || 15;
  const videoUrl = firstStep.video_url || null;

  const difficultyClass = () => {
    switch (difficulty.toLowerCase()) {
      case "easy":
        return styles.easy;
      case "medium":
        return styles.medium;
      case "hard":
        return styles.hard;
      default:
        return styles.easy;
    }
  };

  return (
    <div className={styles.container}>
      <details className={styles.details} open={isOpen} onToggle={(e) => setIsOpen(e.currentTarget.open)}>
        <summary className={styles.summary}>
          <div className={styles.header}>
            <span className={styles.title}>
              🔧 Installation Steps for {data.ps_number}
            </span>
            <div className={styles.meta}>
              <span className={`${styles.badge} ${difficultyClass()}`}>
                {difficulty}
              </span>
              <span className={styles.time}>
                ⏱️ {estMinutes} mins
              </span>
              <span className={styles.arrow}>{isOpen ? "▲" : "▼"}</span>
            </div>
          </div>
        </summary>

        <div className={styles.content}>
          <ol className={styles.stepList}>
            {data.steps.map((step) => (
              <li key={step.step_no} className={styles.stepItem}>
                <div className={styles.stepNumber}>{step.step_no}</div>
                <div className={styles.stepText}>{step.text}</div>
              </li>
            ))}
          </ol>

          {videoUrl && (
            <div className={styles.videoLinkContainer}>
              <a
                href={videoUrl}
                target="_blank"
                rel="noopener noreferrer"
                className={styles.videoButton}
              >
                <svg viewBox="0 0 24 24" className={styles.videoIcon}>
                  <path fill="currentColor" d="M17,10.5V7A1,1 0 0,0 16,6H4A1,1 0 0,0 3,7V17A1,1 0 0,0 4,18H16A1,1 0 0,0 17,17V13.5L21,17.5V6.5L17,10.5Z" />
                </svg>
                Watch Video Tutorial
              </a>
            </div>
          )}
        </div>
      </details>
    </div>
  );
}
