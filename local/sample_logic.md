# Human Evaluation Data Selection

To construct the human evaluation set, we applied a reproducible filtering and sampling pipeline to the WMT test data for Assamese, Meitei Mayek, and Nagamese. Each sentence pair was first normalized by removing invisible Unicode characters, collapsing repeated whitespace, and trimming surrounding spaces. We then removed exact and near-duplicate pairs using a normalized parallel-pair key to reduce redundancy in the final set.

For each source sentence \(x_i\), we computed the source-side word count \(L_i\) using whitespace tokenization and estimated the mean source length over the deduplicated pool as

$$
\mu = \frac{1}{N}\sum_{i=1}^{N} L_i
$$

We retained sentence pairs whose source length satisfied

$$
\lfloor \mu \rfloor \leq L_i \leq \lfloor \mu + 5 \rfloor
$$

This range was chosen to prefer moderately informative examples while avoiding very short trivial cases and overly long sentences that are harder to evaluate consistently. In addition, we excluded rows containing URLs or email addresses, as well as rows with unusually high punctuation or digit ratios, since these often indicate noise, metadata, or malformed text.

The filtering pipeline used two stages of heuristic thresholds. Under the primary filter, sentence pairs were retained only if the punctuation ratio was \(\leq 0.35\) and the digit ratio was \(\leq 0.30\). If fewer than 30 candidates remained after primary filtering, a fallback filter was applied with slightly relaxed thresholds: punctuation ratio \(\leq 0.40\) and digit ratio \(\leq 0.35\). For a sentence \(s\), these were computed as

$$
\mathrm{punct\_ratio}(s) = \frac{N_{\mathrm{punct}}(s)}{\lvert s \rvert}
$$

$$
\mathrm{digit\_ratio}(s) = \frac{N_{\mathrm{digit}}(s)}{\lvert s \rvert}
$$

If the resulting candidate pool contained fewer than 30 items, we also relaxed the source-length window to

$$
[\lfloor \mu - 2 \rfloor, \lfloor \mu + 8 \rfloor]
$$

Finally, we sampled 30 items per language using a fixed random seed to ensure exact reproducibility across runs. The selected items were exported in full CSV format, a minimized CSV containing only source and target text, and JSON format for direct deployment in annotation workflows.
