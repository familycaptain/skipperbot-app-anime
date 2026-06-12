// UI manifest for the anime app. Auto-discovered by
// web/src/apps/registry.js via import.meta.glob at build time.
import { lazy } from "react";
import { Tv, PlayCircle } from "lucide-react";

export default [
  { id: "anime", name: "Anime", icon: Tv, component: lazy(() => import("./AnimeApp")), singleton: true },
  { id: "anime-player", name: "Anime Player", icon: PlayCircle, component: lazy(() => import("./AnimePlayerApp")), singleton: false, hidden: true },
];
