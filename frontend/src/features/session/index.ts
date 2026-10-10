/**
 * Start-up and the profile: who is looking, and the two hooks every screen uses for the
 * current user and the product's tunables.
 */
export { AppGate, useWelcomeOpen } from "./app-gate";
export { ProfileForm, type ProfileFormProps } from "./profile-form";
export { useConfig, useMe } from "@/lib/api/session";
