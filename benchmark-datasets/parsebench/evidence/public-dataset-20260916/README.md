# Public Dataset acceptance

This release removes candidate-runtime coupling from the public ParseBench
Dataset. It preserves the 2,078 inputs, 169,011 rules and fixed official scorer.
Both task environments build from the public Python base recorded in the
source/image evidence. No private AWorld/FileX image or wheel is required.

The complete converter suite passed 53 tests against the final ARM64 scorer
image, including all source material and the embedded selection pin. Both ARM64
and AMD64 scorer Dockerfiles built successfully and passed offline source and
dependency probes. Synthetic positive/negative artifacts exercise the official
five primary metrics; they are integration checks, not benchmark results.

The earlier local Dataset package was only structurally validated and could not
be built by a remote builder. This release additionally builds task/verifier
images and runs the scorer without network access. All images in this evidence
are local validation images; Dataset Dockerfiles use public upstream bases.
