// Tableau blanc d'AxiorHub (5.6.103, lot T) : ce qu'emploie agent/static/v56103-tableau.js, et rien d'autre.
// Versions exactes dans le fichier de verrouillage voisin (@excalidraw/excalidraw 0.18.1, React 18.3.1). Le convertisseur Mermaid
// (@excalidraw/mermaid-to-excalidraw, et Mermaid derrière lui) n'est chargé qu'à la première proposition de Pilote : morceau séparé.
// Les polices sont lues depuis window.EXCALIDRAW_ASSET_PATH, posé par v56103-tableau.js avant l'import ; jamais depuis un CDN.
import React from 'react';
import { createRoot } from 'react-dom/client';
import {
  Excalidraw, MainMenu, CaptureUpdateAction, convertToExcalidrawElements, exportToBlob, exportToSvg, reconcileElements, restoreElements,
  getSceneVersion, getCommonBounds,
} from '@excalidraw/excalidraw';
import '@excalidraw/excalidraw/index.css';

export { React, Excalidraw, MainMenu, CaptureUpdateAction, convertToExcalidrawElements, exportToBlob, exportToSvg, reconcileElements, restoreElements,
  getSceneVersion, getCommonBounds };

// Monte le composant dans un nœud du volet ; rend une fonction qui démonte (fermeture du volet, changement de discussion).
export function monter(noeud, proprietes) {
  const racine = createRoot(noeud);
  racine.render(React.createElement(Excalidraw, proprietes));
  return () => racine.unmount();
}

// Convertisseur Mermaid → squelettes Excalidraw, dans le navigateur seulement (aucun service distant).
export async function chargerMermaid() {
  const m = await import('@excalidraw/mermaid-to-excalidraw');
  return m.parseMermaidToExcalidraw;
}
