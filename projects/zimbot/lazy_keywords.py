"""
Lazy Keyword Extraction

Extracts keywords from user conversations for targeted lazy embedding.
Uses TF-IDF for efficient keyword extraction without external dependencies
beyond scikit-learn (already installed).
"""

import re
from typing import List, Set
from sklearn.feature_extraction.text import TfidfVectorizer


class KeywordExtractor:
    """
    Extracts keywords from text using TF-IDF scoring.

    Designed for extracting search keywords from user questions
    to find related content in ZIM archives.
    """

    # Common words to filter out (beyond sklearn's english stopwords)
    EXTRA_STOPWORDS = {
        'what', 'how', 'why', 'when', 'where', 'who', 'which',
        'can', 'could', 'would', 'should', 'does', 'did', 'do',
        'is', 'are', 'was', 'were', 'be', 'been', 'being',
        'have', 'has', 'had', 'having',
        'tell', 'explain', 'describe', 'know', 'think', 'mean',
        'please', 'thanks', 'thank', 'help', 'need', 'want',
        'question', 'answer', 'information', 'about', 'regarding',
        'also', 'just', 'like', 'really', 'very', 'much', 'many',
        'some', 'any', 'more', 'most', 'other', 'each', 'every',
    }

    def __init__(self, max_keywords: int = 5, min_word_length: int = 3):
        """
        Initialize the keyword extractor.

        Args:
            max_keywords: Maximum number of keywords to extract
            min_word_length: Minimum word length to consider
        """
        self.max_keywords = max_keywords
        self.min_word_length = min_word_length

        # TF-IDF vectorizer for keyword extraction
        self.vectorizer = TfidfVectorizer(
            max_features=500,
            stop_words='english',
            ngram_range=(1, 2),  # Unigrams and bigrams
            min_df=1,
            token_pattern=r'\b[a-zA-Z][a-zA-Z0-9\-]*[a-zA-Z0-9]\b|\b[a-zA-Z]{2,}\b'
        )

    def extract_keywords(self, text: str) -> List[str]:
        """
        Extract top keywords from text using TF-IDF scoring.

        Args:
            text: Input text (typically a user question)

        Returns:
            List of extracted keywords, ordered by importance
        """
        # Clean and normalize text
        text = self._preprocess_text(text)

        if not text or len(text) < 5:
            return []

        try:
            # Fit and transform on single document
            tfidf_matrix = self.vectorizer.fit_transform([text])

            # Get feature names and scores
            feature_names = self.vectorizer.get_feature_names_out()
            scores = tfidf_matrix.toarray()[0]

            # Create scored keyword list
            keyword_scores = list(zip(feature_names, scores))
            keyword_scores.sort(key=lambda x: x[1], reverse=True)

            # Filter and return top keywords
            keywords = []
            seen_stems = set()

            for kw, score in keyword_scores:
                if score <= 0:
                    continue

                # Skip if too short
                if len(kw) < self.min_word_length:
                    continue

                # Skip extra stopwords
                if kw.lower() in self.EXTRA_STOPWORDS:
                    continue

                # Skip if we've seen a similar stem (simple dedup)
                stem = kw[:4].lower() if len(kw) > 4 else kw.lower()
                if stem in seen_stems:
                    continue
                seen_stems.add(stem)

                keywords.append(kw)

                if len(keywords) >= self.max_keywords:
                    break

            return keywords

        except Exception:
            # Fall back to simple extraction on error
            return self._simple_extract(text)

    def _preprocess_text(self, text: str) -> str:
        """Clean and normalize text for keyword extraction."""
        # Convert to lowercase
        text = text.lower()

        # Remove URLs
        text = re.sub(r'https?://\S+', '', text)

        # Remove special characters but keep spaces and hyphens
        text = re.sub(r'[^\w\s\-]', ' ', text)

        # Normalize whitespace
        text = re.sub(r'\s+', ' ', text).strip()

        return text

    def _simple_extract(self, text: str) -> List[str]:
        """
        Simple fallback extraction when TF-IDF fails.
        Just returns longest unique words.
        """
        words = text.split()
        seen = set()
        keywords = []

        # Sort by length (longer words tend to be more specific)
        words_sorted = sorted(words, key=len, reverse=True)

        for word in words_sorted:
            word_clean = re.sub(r'[^\w\-]', '', word).lower()

            if len(word_clean) < self.min_word_length:
                continue

            if word_clean in self.EXTRA_STOPWORDS:
                continue

            if word_clean in seen:
                continue

            seen.add(word_clean)
            keywords.append(word_clean)

            if len(keywords) >= self.max_keywords:
                break

        return keywords

    def extract_entities(self, text: str) -> List[str]:
        """
        Extract capitalized phrases that might be important entities.
        Useful for catching proper nouns like "Quantum Computing" or "Albert Einstein".
        """
        # Match capitalized words/phrases (2+ words starting with caps)
        pattern = r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b'
        entities = re.findall(pattern, text)

        # Also get single capitalized words that aren't at sentence start
        single_caps = re.findall(r'(?<=[.!?]\s)[A-Z][a-z]+\b|(?<=\s)[A-Z][a-z]{3,}\b', text)
        entities.extend(single_caps)

        # Dedupe and limit
        seen = set()
        unique_entities = []
        for entity in entities:
            entity_lower = entity.lower()
            if entity_lower not in seen:
                seen.add(entity_lower)
                unique_entities.append(entity)

        return unique_entities[:3]

    def extract_all(self, text: str) -> List[str]:
        """
        Extract both TF-IDF keywords and entities, combined.
        Returns a deduplicated list with entities prioritized.
        """
        entities = self.extract_entities(text)
        keywords = self.extract_keywords(text)

        # Combine with entities first
        seen = set(e.lower() for e in entities)
        combined = list(entities)

        for kw in keywords:
            if kw.lower() not in seen:
                combined.append(kw)
                seen.add(kw.lower())

        return combined[:self.max_keywords]
