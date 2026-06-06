"use client";
import React from "react";
import styles from "./CompatVerdict.module.css";

interface CompatData {
  compatible?: boolean;
  ps_number?: string;
  model_number?: string;
  part_appliance_type?: string;
  model_appliance_type?: string;
  error?: string;
  hint?: string;
}

export function CompatVerdict({ data }: { data: CompatData }) {
  if (data.error) {
    return (
      <div className={`${styles.card} ${styles.cardBad}`}>
        <div className={styles.iconContainer}>
          <span className={styles.icon}>✗</span>
        </div>
        <div className={styles.info}>
          <h4 className={styles.title}>
            {data.error === "model_not_found"
              ? `Model not found: ${data.model_number}`
              : `Part not found: ${data.ps_number}`}
          </h4>
          {data.hint && <p className={styles.hint}>{data.hint}</p>}
        </div>
      </div>
    );
  }

  const isCompatible = data.compatible;
  const isTypeMismatch = data.part_appliance_type !== data.model_appliance_type;

  return (
    <div className={`${styles.card} ${isCompatible ? styles.cardGood : styles.cardBad}`}>
      <div className={styles.iconContainer}>
        <span className={styles.icon}>{isCompatible ? "✓" : "✗"}</span>
      </div>
      <div className={styles.info}>
        <h4 className={styles.title}>
          {isCompatible
            ? `Compatible: Fits Model ${data.model_number}`
            : `Incompatible with Model ${data.model_number}`}
        </h4>
        <p className={styles.details}>
          Part {data.ps_number} is {isCompatible ? "" : "NOT "} compatible with model {data.model_number}.
        </p>
        
        {isTypeMismatch && (
          <div className={styles.mismatchBox}>
            <span className={styles.warningIcon}>⚠️</span>
            <p className={styles.warningText}>
              <strong>Type Mismatch:</strong> This is a <strong>{data.part_appliance_type}</strong> part, but model {data.model_number} is a <strong>{data.model_appliance_type}</strong>.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
