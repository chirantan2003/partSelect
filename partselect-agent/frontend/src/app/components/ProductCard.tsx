"use client";
import React, { useState } from "react";
import styles from "./ProductCard.module.css";

interface PartData {
  ps_number: string;
  name: string;
  description?: string;
  price_cents: number;
  in_stock: boolean;
  image_url?: string;
  rating?: string | number;
  review_count?: number;
  appliance_type?: string;
}

export function ProductCard({ data }: { data: PartData }) {
  const [added, setAdded] = useState(false);

  const handleAddToCart = () => {
    setAdded(true);
    setTimeout(() => {
      setAdded(false);
    }, 2000);
  };

  const formattedPrice = (data.price_cents / 100).toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
  });

  const ratingStars = (rating: number = 0) => {
    const stars = [];
    const rounded = Math.round(rating);
    for (let i = 1; i <= 5; i++) {
      stars.push(
        <span key={i} className={i <= rounded ? styles.starFilled : styles.starEmpty}>
          ★
        </span>
      );
    }
    return stars;
  };

  return (
    <div className={styles.card}>
      <div className={styles.imageContainer}>
        {data.image_url ? (
          <img src={data.image_url} alt={data.name} className={styles.image} />
        ) : (
          <div className={styles.placeholder}>
            <svg viewBox="0 0 24 24" className={styles.placeholderIcon}>
              <path fill="currentColor" d="M19,4H15V2H9V4H5A2,2 0 0,0 3,6V20A2,2 0 0,0 5,22H19A2,2 0 0,0 21,20V6A2,2 0 0,0 19,4M9,20H5V8H9V20M13,20H11V8H13V20M19,20H15V8H19V20Z" />
            </svg>
          </div>
        )}
        {data.appliance_type && (
          <span className={styles.typeTag}>{data.appliance_type}</span>
        )}
      </div>

      <div className={styles.content}>
        <div className={styles.header}>
          <span className={styles.psNumber}>{data.ps_number}</span>
          <span className={data.in_stock ? styles.stockIn : styles.stockOut}>
            {data.in_stock ? "In Stock" : "Out of Stock"}
          </span>
        </div>

        <h3 className={styles.name}>{data.name}</h3>
        
        {data.rating !== undefined && data.rating !== null && (
          <div className={styles.ratingRow}>
            <span className={styles.stars}>{ratingStars(Number(data.rating))}</span>
            <span className={styles.ratingText}>
              {Number(data.rating).toFixed(1)} ({data.review_count || 0})
            </span>
          </div>
        )}

        {data.description && <p className={styles.description}>{data.description}</p>}

        <div className={styles.footer}>
          <span className={styles.price}>{formattedPrice}</span>
          <button
            onClick={handleAddToCart}
            className={added ? styles.addedButton : styles.addButton}
            disabled={!data.in_stock}
          >
            {added ? "✓ Added" : "Add to Cart"}
          </button>
        </div>
      </div>
    </div>
  );
}
