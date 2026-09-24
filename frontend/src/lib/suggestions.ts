/** Curated, source-tested starters. Labels stay short; questions preserve retrieval context. */
type LocalizedSuggestion = {
  label: string;
  question: string;
};

type Suggestion = {
  id: string;
  sq: LocalizedSuggestion;
  en: LocalizedSuggestion;
};

export type SelectedSuggestion = { id: string } & LocalizedSuggestion;

export const suggestionBank: readonly Suggestion[] = [
  {
    id: "movement",
    sq: { label: "Revolucioni Flamingo?", question: "Çfarë është Revolucioni Flamingo?" },
    en: {
      label: "Flamingo Revolution?",
      question: "What is the Flamingo Revolution? Answer in English.",
    },
  },
  {
    id: "diaspora",
    sq: { label: "Diaspora Zbarkon?", question: "Çfarë është Diaspora Zbarkon?" },
    en: { label: "Diaspora Zbarkon?", question: "What is Diaspora Zbarkon? Answer in English." },
  },
  {
    id: "pulsi",
    sq: { label: "Çfarë tregon Pulsi?", question: "Çfarë tregon Pulsi i protestës?" },
    en: {
      label: "What does Pulsi show?",
      question: "What does the Pulsi protest participation index measure? Answer in English.",
    },
  },
  {
    id: "peak_day",
    sq: {
      label: "Dita më e madhe?",
      question: "Cila ishte dita më e madhe sipas indeksit Pulsi?",
    },
    en: {
      label: "Peak protest day?",
      question: "Which day scored 100 on the Pulsi index? Answer in English.",
    },
  },
  {
    id: "referendum",
    sq: { label: "Referendumi 21/2024?", question: "Çfarë është Referendumi 21/2024?" },
    en: {
      label: "Referendum 21/2024?",
      question: "What is Referendum 21/2024? Answer in English.",
    },
  },
  {
    id: "protest_map",
    sq: {
      label: "Sa qytete në hartë?",
      question: "Sa qytete dhe shtete ka Harta e Protestave?",
    },
    en: {
      label: "Cities on the map?",
      question: "How many cities and countries are on Harta e Protestave? Answer in English.",
    },
  },
  {
    id: "vlora_airport",
    sq: { label: "Aeroporti i Vlorës?", question: "Çfarë ndodhi me Aeroportin e Vlorës?" },
    en: {
      label: "Vlora Airport?",
      question: "What happened with Vlora Airport? Answer in English.",
    },
  },
  {
    id: "diaspora_help",
    sq: { label: "Si ndihmon diaspora?", question: "Si mund të ndihmojë diaspora?" },
    en: {
      label: "How can diaspora help?",
      question: "How can the Albanian diaspora help? Answer in English.",
    },
  },
  {
    id: "dossier",
    sq: { label: "Dosja Flamingo?", question: "Çfarë është Dosja Flamingo?" },
    en: {
      label: "Flamingo Dossier?",
      question: "What is the Flamingo Dossier? Answer in English.",
    },
  },
  {
    id: "zvernec",
    sq: { label: "Çfarë ndodhi në Zvërnec?", question: "Çfarë ndodhi në Zvërnec?" },
    en: {
      label: "What happened in Zvërnec?",
      question: "What happened in Zvërnec? Answer in English.",
    },
  },
  {
    id: "why_protesting",
    sq: {
      label: "Pse protestojnë shqiptarët?",
      question: "Pse protestojnë shqiptarët me Revolucionin Flamingo?",
    },
    en: {
      label: "Why are Albanians protesting?",
      question: "Why are Albanians protesting with the Flamingo Revolution? Answer in English.",
    },
  },
];

/** Pick two different prompts afresh when an empty chat opens. */
export function selectSuggestions(
  pageLanguage: string,
  random: () => number = Math.random,
): SelectedSuggestion[] {
  const locale = /^en(?:-|$)/i.test(pageLanguage.trim()) ? "en" : "sq";
  const pool = [...suggestionBank];
  for (let index = 0; index < 2; index += 1) {
    const swapIndex = index + Math.floor(random() * (pool.length - index));
    [pool[index], pool[swapIndex]] = [pool[swapIndex], pool[index]];
  }
  return pool.slice(0, 2).map((entry) => ({ id: entry.id, ...entry[locale] }));
}
