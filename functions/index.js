/**
 * Cloud Function relay for AI resume tailoring.
 *
 * Why this exists: OpenAI's API does not send CORS headers, so a browser
 * cannot call api.openai.com directly from the website — the request would
 * be blocked by the browser itself before it even reaches OpenAI. This tiny
 * function just forwards the request server-side and streams the response
 * back. It does NOT store the API key or resume anywhere — both are passed
 * in on each request and only ever held in memory for the duration of that
 * one request.
 *
 * Deploy with: firebase deploy --only functions
 * Requires the Firebase project to be on the Blaze (pay-as-you-go) plan —
 * Cloud Functions cannot run on the free Spark plan. You are only billed for
 * OpenAI usage (via your own OpenAI account/key) and negligible Cloud
 * Functions invocation costs — see "Set up AI resume tailoring" in DEPLOY.md.
 */

const { onRequest } = require("firebase-functions/v2/https");
const cors = require("cors")({ origin: true });

const OPENAI_URL = "https://api.openai.com/v1/chat/completions";

exports.tailorResume = onRequest(
  { region: "us-central1", cors: true, memory: "256MiB", timeoutSeconds: 60 },
  (req, res) => {
    cors(req, res, async () => {
      if (req.method !== "POST") {
        res.status(405).json({ error: "Use POST." });
        return;
      }

      const { apiKey, resumeText, job } = req.body || {};

      if (!apiKey || typeof apiKey !== "string" || !apiKey.startsWith("sk-")) {
        res.status(400).json({ error: "Missing or invalid OpenAI API key." });
        return;
      }
      if (!resumeText || typeof resumeText !== "string") {
        res.status(400).json({ error: "Missing resume text." });
        return;
      }
      if (!job || !job.title) {
        res.status(400).json({ error: "Missing job details." });
        return;
      }

      const prompt = [
        "You are an expert resume editor. Rewrite the resume below so it is",
        "tightly tailored to the specific job listed, while staying 100% truthful",
        "to the candidate's real experience — never invent employers, titles,",
        "dates, degrees, or skills that are not already present in the original.",
        "You MAY: reorder bullet points, rephrase for stronger action verbs and",
        "quantified impact, emphasize the most relevant existing experience/skills,",
        "and tighten a summary section to mirror the job's language.",
        "You MUST NOT: fabricate experience, or change facts (companies, dates, degrees).",
        "",
        `Job title: ${job.title}`,
        `Company: ${job.company || "Unknown"}`,
        `Location: ${job.location || "Unknown"}`,
        `Source: ${job.source || "Unknown"}`,
        "",
        "Note: only the job title/company/location are available here (no full",
        "job description was scraped), so tailor based on what this role title",
        "and company typically require, while staying grounded in the resume's",
        "real content.",
        "",
        "Original resume text:",
        "---",
        resumeText,
        "---",
        "",
        "Output ONLY the rewritten resume text (plain text, no commentary, no",
        "markdown formatting, no explanation before or after).",
      ].join("\n");

      try {
        const openaiResp = await fetch(OPENAI_URL, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${apiKey}`,
          },
          body: JSON.stringify({
            model: "gpt-4o-mini",
            messages: [{ role: "user", content: prompt }],
            temperature: 0.4,
          }),
        });

        const data = await openaiResp.json();

        if (!openaiResp.ok) {
          const message = (data && data.error && data.error.message) || `OpenAI error (HTTP ${openaiResp.status})`;
          res.status(openaiResp.status).json({ error: message });
          return;
        }

        const tailoredResume = data.choices && data.choices[0] && data.choices[0].message
          ? data.choices[0].message.content
          : "";

        res.status(200).json({ tailoredResume });
      } catch (err) {
        res.status(500).json({ error: "Server error calling OpenAI: " + err.message });
      }
    });
  }
);
