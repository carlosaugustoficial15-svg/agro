/// <reference types="vite/client" />
interface Window { studio: { saveProject(project: unknown): Promise<string | null>; openProject(): Promise<any>; setGoogleKey(key: string): Promise<boolean>; hasGoogleKey(): Promise<boolean> } }
