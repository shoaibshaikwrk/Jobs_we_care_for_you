// Firebase web app config — this is NOT a secret (Firebase web configs are meant
// to be public; access is controlled by Firestore security rules and the
// "Authorized domains" list in Firebase Auth, not by hiding this file).
//
// This file is committed once and edited by hand — job_checker.py never
// touches it, so it survives every daily automated regeneration of index.html.
//
// How to fill this in: see "Set up Firebase" in DEPLOY.md. In short:
//   1. Create a free project at https://console.firebase.google.com
//   2. Project settings (gear icon) → General → "Your apps" → Add app → Web (</>)
//   3. Copy the firebaseConfig object it gives you and paste the values below.

const firebaseConfig = {
  apiKey: "AIzaSyB0CfPFlbNeGu_jq12JjJcXIq3d9d5PBdU",
  authDomain: "jobs-we-care-you.firebaseapp.com",
  projectId: "jobs-we-care-you",
  storageBucket: "jobs-we-care-you.firebasestorage.app",
  messagingSenderId: "109056475031",
  appId: "1:109056475031:web:3ea598382db0a8863e0ed2",

  // Base URL of the deployed "tailorResume" Cloud Function, used by the
  // Settings → "Tailor" button for AI resume tailoring. Leave this as an
  // empty string until you've deployed the function (see "Set up AI resume
  // tailoring" in DEPLOY.md) — the Tailor button will show a helpful message
  // instead of failing silently until this is filled in. Once deployed, the
  // Firebase CLI prints the exact URL to use, e.g.:
  //   https://us-central1-jobs-we-care-you.cloudfunctions.net/tailorResume
  // Set this to everything BEFORE the trailing "/tailorResume", e.g.:
  //   https://us-central1-jobs-we-care-you.cloudfunctions.net
  cloudFunctionsBaseUrl: ""
};
