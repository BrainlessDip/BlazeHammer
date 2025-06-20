from faker.providers import BaseProvider


class SimpleExampleProvider(BaseProvider):
    def simple_example(self, category=None):
        """
        Generate basic text (greetings, farewells, etc.).
        """
        texts = {
            "greetings": ["hi", "hello", "hey", "howdy", "g'day"],
            "farewells": ["bye", "goodbye", "see you", "take care", "later"],
            "positive": ["yes", "sure", "absolutely", "of course", "definitely"],
            "negative": ["no", "nope", "never", "not at all", "not really"],
        }

        if category and category in texts:
            return self.random_element(texts[category])

        return self.random_element(
            texts["greetings"]
        )  # Default to greeting if no category


class AdvancedExampleProvider(BaseProvider):
    def advanced_example(
        self, category=None, language="en", length=5, complexity="medium"
    ):
        """
        Generate random text with varying complexity and language options.
        """

        # Language-specific data
        texts = {
            "en": {
                "greetings": ["hi", "hello", "hey", "howdy", "g'day"],
                "farewells": ["bye", "goodbye", "see you", "take care", "later"],
                "positive": ["yes", "sure", "absolutely", "of course", "definitely"],
                "negative": ["no", "nope", "never", "not at all", "not really"],
                "names": ["John", "Alice", "Bob", "Emily", "Charlie"],
                "places": [
                    "New York",
                    "London",
                    "Paris",
                    "Dhaka",
                    "Tokyo",
                ],  # Corrected 'places'
            },
            "bn": {
                "greetings": ["হ্যালো", "নমস্কার", "হাই", "কেমন আছো", "শুভ সকাল"],
                "farewells": ["বিদায়", "বাই", "আলবিদা", "ফিরে দেখা হবে", "যত্নে থাকো"],
                "positive": ["হ্যাঁ", "অবশ্যই", "পাকা", "নিশ্চিত", "কোনো সন্দেহ নেই"],
                "negative": ["না", "কখনোই", "বিলকুল না", "এটা সম্ভব না", "ঠিক না"],
                "names": ["রাহুল", "মিনা", "আলিফ", "রেহান", "মেহের"],
                "places": [
                    "ঢাকা",
                    "চট্টগ্রাম",
                    "রাজশাহী",
                    "বরিশাল",
                    "কুমিল্লা",
                ],  # Corrected 'places'
            },
        }

        # Select the appropriate texts based on language
        language_texts = texts.get(language, texts["en"])

        # Generate based on category
        if category:
            category_texts = language_texts.get(category, [])
            if category_texts:
                return self.random_element(category_texts)

        # Generate sentences based on complexity
        if complexity == "low":
            return self._generate_simple_sentence(language_texts, length)
        elif complexity == "medium":
            return self._generate_medium_sentence(language_texts, length)
        elif complexity == "high":
            return self._generate_complex_sentence(language_texts, length)

        # Default to medium complexity
        return self._generate_medium_sentence(language_texts, length)

    def _generate_simple_sentence(self, language_texts, length):
        """Generate a simple sentence with random words."""
        words = language_texts["greetings"] + language_texts["positive"]
        return " ".join([self.random_element(words) for _ in range(length)])

    def _generate_medium_sentence(self, language_texts, length):
        """Generate a sentence with context like names and places."""
        names = language_texts["names"]
        places = language_texts["places"]  # Ensure 'places' is used correctly
        sentence = f"{self.random_element(names)} is at {self.random_element(places)}."
        return sentence

    def _generate_complex_sentence(self, language_texts, length):
        """Generate a complex sentence using mixed categories."""
        names = language_texts["names"]
        greetings = language_texts["greetings"]
        farewells = language_texts["farewells"]
        positive_responses = language_texts["positive"]
        places = language_texts["places"]  # Ensure 'places' is used correctly

        sentence = f"{self.random_element(greetings)}! {self.random_element(names)} is thinking of leaving {self.random_element(places)} soon. {self.random_element(farewells)}? {self.random_element(positive_responses)}!"
        return sentence
