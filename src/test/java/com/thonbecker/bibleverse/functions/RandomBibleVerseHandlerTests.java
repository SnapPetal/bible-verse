package com.thonbecker.bibleverse.functions;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.thonbecker.bibleverse.functions.RandomBibleVerseHandler.IndexedVerse;
import com.thonbecker.bibleverse.model.RandomBibleVerseResponse;
import com.thonbecker.bibleverse.service.FileService;
import java.io.FileInputStream;
import java.util.List;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.mockito.Mockito;

class RandomBibleVerseHandlerTests {
    private static RandomBibleVerseHandler handler;

    @BeforeAll
    static void loadBible() throws Exception {
        FileService fileService = Mockito.mock(FileService.class);
        Mockito.when(fileService.getFile("csb/bible.xml"))
                .thenReturn(new FileInputStream(".infrastructure/lib/data/files/csb/bible.xml"));
        Mockito.when(fileService.getFile("greek/bible.xml"))
                .thenReturn(new FileInputStream(".infrastructure/lib/data/files/greek/bible.xml"));
        handler = new RandomBibleVerseHandler(fileService);
    }

    @Test
    void indexesEveryCsbVerseAndKnownGreekGaps() {
        List<IndexedVerse> verses = handler.indexedVerses();
        assertFalse(verses.isEmpty());

        List<String> missingGreek = verses.stream()
                .filter(verse -> verse.greek() == null)
                .map(verse -> verse.book() + " " + verse.chapter() + ":" + verse.number())
                .toList();
        assertEquals(List.of("3 John 1:15", "Revelation 12:18"), missingGreek);

        for (IndexedVerse verse : verses) {
            assertFalse(verse.csb().isBlank(), verse.book() + " " + verse.chapter() + ":" + verse.number());
            if (verse.greek() != null) {
                assertFalse(verse.greek().isBlank(), verse.book() + " " + verse.chapter() + ":" + verse.number());
            }
        }
    }

    @Test
    void omitsGreekWhenTheTranslationHasNoMatchingVerse() {
        IndexedVerse gap = handler.indexedVerses().stream()
                .filter(verse -> verse.greek() == null)
                .findFirst()
                .orElseThrow();

        RandomBibleVerseResponse response = handler.toResponse(gap);

        assertEquals("3 John", response.getBook());
        assertEquals("1", response.getChapter());
        assertEquals("15", response.getVerse());
        assertTrue(response.getText().containsKey("CSB"));
        assertFalse(response.getText().get("CSB").isBlank());
        assertFalse(response.getText().containsKey("Greek"));
    }

    @Test
    void randomVerseMatchesTheIndex() {
        RandomBibleVerseResponse response = handler.get();

        assertTrue(handler.indexedVerses().stream()
                .anyMatch(verse -> verse.book().equals(response.getBook())
                        && verse.chapter().equals(response.getChapter())
                        && verse.number().equals(response.getVerse())
                        && verse.csb().equals(response.getText().get("CSB"))));
        assertFalse(response.getText().get("CSB").isBlank());
        if (response.getText().containsKey("Greek")) {
            assertFalse(response.getText().get("Greek").isBlank());
        }
    }
}
