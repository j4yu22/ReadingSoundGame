import { book } from "./book.js";


function getRandomKey(object) {
  /**
   * Returns a random key from the provided object.
   *
   * Parameters:
   *        object (Object): Source object to pick a key from
   *
   * Returns:
   *        key (string): Randomly selected key
   */
  const keys = Object.keys(object);
  const randomIndex = Math.floor(Math.random() * keys.length);
  return keys[randomIndex];
}


function getRandomActivity(sectionKey) {
  /**
   * Returns a random activity from the specified book section.
   *
   * Parameters:
   *        sectionKey (string): Section key in the book, such as "D1" or "G2"
   *
   * Returns:
   *        activityData (Object): Selected section metadata and activity content
   */
  const section = book[sectionKey];

  if (!section) {
    return null;
  }

  const pageKeys = Object.keys(section).filter((key) => key !== "type");
  const randomPageKey = getRandomKey(
    Object.fromEntries(pageKeys.map((key) => [key, section[key]]))
  );

  const page = section[randomPageKey];
  const itemKey = getRandomKey(page);
  const activity = page[itemKey];

  return {
    sectionKey,
    type: section.type,
    pageKey: randomPageKey,
    itemKey,
    activity
  };
}


function buildDeletionText(activity) {
  /**
   * Builds the display text for a deletion activity.
   *
   * Parameters:
   *        activity (Object): Deletion activity containing the original word, omitted part, and solution
   *
   * Returns:
   *        text (string): Formatted deletion example text
   */
  return `Say ${activity.original}. [${activity.original}] Now say ${activity.original} but don't say ${activity.omit}. [${activity.solution}]`;
}


function buildSubstitutionText(activity) {
  /**
   * Builds the display text for a substitution activity.
   *
   * Parameters:
   *        activity (Object): Substitution activity containing the original word, omitted sound, replacement sound, and solution
   *
   * Returns:
   *        text (string): Formatted substitution example text
   */
  return `Say ${activity.original}. [${activity.original}] Now say ${activity.original} but instead of ${activity.omit}, say ${activity.replace}. [${activity.solution}]`;
}


function buildExampleText(sectionKey) {
  /**
   * Selects a random activity from the given section and returns the formatted text.
   *
   * Parameters:
   *        sectionKey (string): Section key in the book, such as "D1" or "G2"
   *
   * Returns:
   *        text (string): Formatted activity text
   */
  const selection = getRandomActivity(sectionKey);

  if (!selection) {
    return "Section not found.";
  }

  if (selection.type === "deletion") {
    return buildDeletionText(selection.activity);
  }

  if (selection.type === "substitution") {
    return buildSubstitutionText(selection.activity);
  }

  return "Unknown activity type.";
}

export function buildRandomExampleText() {
  /**
   * Selects a random section from the book, then selects and formats a random activity from it.
   *
   * Parameters:
   *        None
   *
   * Returns:
   *        text (string): Formatted activity text
   */
  const sectionKey = getRandomKey(book);
  return buildExampleText(sectionKey);
}

export function getRandomExample() {
  const sectionKey = getRandomKey(book);
  const selection = getRandomActivity(sectionKey);

  if (!selection) {
    return null;
  }

  return selection;
}


export function buildStagePrompt(selection, stage) {
  const activity = selection.activity;

  if (stage === 1) {
    return `Say ${activity.original}.`;
  }

  if (stage === 2 && selection.type === "deletion") {
    return `Now say ${activity.original}, but don't say ${activity.omit}.`;
  }

  if (stage === 2 && selection.type === "substitution") {
    return `Now say ${activity.original}, but instead of ${activity.omit}, say ${activity.replace}.`;
  }

  return "";
}