package com.thonbecker.bibleverse.functions;

import com.thonbecker.bibleverse.model.RandomBibleVerseResponse;
import com.thonbecker.bibleverse.service.ConditionalOnRandomVerse;
import com.thonbecker.bibleverse.service.FileService;
import java.io.InputStream;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ThreadLocalRandom;
import java.util.function.Supplier;
import javax.xml.XMLConstants;
import javax.xml.parsers.DocumentBuilderFactory;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;
import org.w3c.dom.Document;
import org.w3c.dom.Element;
import org.w3c.dom.NodeList;

@Component
@ConditionalOnRandomVerse
@Slf4j
public class RandomBibleVerseHandler implements Supplier<RandomBibleVerseResponse> {
    private final List<IndexedVerse> verses;

    private static final List<String> BOOK_NAMES = List.of(
            "Genesis",
            "Exodus",
            "Leviticus",
            "Numbers",
            "Deuteronomy",
            "Joshua",
            "Judges",
            "Ruth",
            "1 Samuel",
            "2 Samuel",
            "1 Kings",
            "2 Kings",
            "1 Chronicles",
            "2 Chronicles",
            "Ezra",
            "Nehemiah",
            "Esther",
            "Job",
            "Psalms",
            "Proverbs",
            "Ecclesiastes",
            "Song of Solomon",
            "Isaiah",
            "Jeremiah",
            "Lamentations",
            "Ezekiel",
            "Daniel",
            "Hosea",
            "Joel",
            "Amos",
            "Obadiah",
            "Jonah",
            "Micah",
            "Nahum",
            "Habakkuk",
            "Zephaniah",
            "Haggai",
            "Zechariah",
            "Malachi",
            "Matthew",
            "Mark",
            "Luke",
            "John",
            "Acts",
            "Romans",
            "1 Corinthians",
            "2 Corinthians",
            "Galatians",
            "Ephesians",
            "Philippians",
            "Colossians",
            "1 Thessalonians",
            "2 Thessalonians",
            "1 Timothy",
            "2 Timothy",
            "Titus",
            "Philemon",
            "Hebrews",
            "James",
            "1 Peter",
            "2 Peter",
            "1 John",
            "2 John",
            "3 John",
            "Jude",
            "Revelation");

    public RandomBibleVerseHandler(FileService fileService) {
        try {
            Document csbDoc = parseXml(fileService.getFile("csb/bible.xml"));
            Document greekDoc = parseXml(fileService.getFile("greek/bible.xml"));
            this.verses = indexBibles(csbDoc, greekDoc);
        } catch (Exception e) {
            throw new RuntimeException("Failed to load Bible XML data", e);
        }
    }

    @Override
    public RandomBibleVerseResponse get() {
        return toResponse(verses.get(ThreadLocalRandom.current().nextInt(verses.size())));
    }

    List<IndexedVerse> indexedVerses() {
        return verses;
    }

    RandomBibleVerseResponse toResponse(IndexedVerse verse) {
        Map<String, String> verseText;
        if (verse.greek != null) {
            verseText = Map.of("CSB", verse.csb, "Greek", verse.greek);
        } else {
            verseText = Map.of("CSB", verse.csb);
            log.info("No Greek data found for {} {}:{}", verse.book, verse.chapter, verse.number);
        }

        return RandomBibleVerseResponse.builder()
                .book(verse.book)
                .chapter(verse.chapter)
                .verse(verse.number)
                .text(verseText)
                .build();
    }

    private Document parseXml(InputStream is) throws Exception {
        try (is) {
            DocumentBuilderFactory factory = DocumentBuilderFactory.newInstance();
            factory.setFeature(XMLConstants.FEATURE_SECURE_PROCESSING, true);
            factory.setFeature("http://apache.org/xml/features/disallow-doctype-decl", true);
            factory.setFeature("http://xml.org/sax/features/external-general-entities", false);
            factory.setFeature("http://xml.org/sax/features/external-parameter-entities", false);
            factory.setAttribute(XMLConstants.ACCESS_EXTERNAL_DTD, "");
            factory.setAttribute(XMLConstants.ACCESS_EXTERNAL_SCHEMA, "");
            factory.setExpandEntityReferences(false);
            factory.setXIncludeAware(false);
            return factory.newDocumentBuilder().parse(is);
        }
    }

    private List<IndexedVerse> indexBibles(Document csbDoc, Document greekDoc) {
        // Index Greek text by book/chapter/verse number for O(1) lookup
        Map<String, Map<String, Map<String, String>>> greekIndex = new HashMap<>();
        NodeList greekBooks = greekDoc.getElementsByTagName("book");
        for (int i = 0; i < greekBooks.getLength(); i++) {
            Element greekBook = (Element) greekBooks.item(i);
            String bookNum = greekBook.getAttribute("number");
            Map<String, Map<String, String>> chapterMap = new HashMap<>();
            greekIndex.put(bookNum, chapterMap);
            NodeList greekChapters = greekBook.getElementsByTagName("chapter");
            for (int j = 0; j < greekChapters.getLength(); j++) {
                Element greekChapter = (Element) greekChapters.item(j);
                String chapterNum = greekChapter.getAttribute("number");
                Map<String, String> verseMap = new HashMap<>();
                chapterMap.put(chapterNum, verseMap);
                NodeList greekVerses = greekChapter.getElementsByTagName("verse");
                for (int k = 0; k < greekVerses.getLength(); k++) {
                    Element greekVerse = (Element) greekVerses.item(k);
                    verseMap.put(greekVerse.getAttribute("number"), greekVerse.getTextContent());
                }
            }
        }

        List<IndexedVerse> result = new ArrayList<>();
        NodeList csbBooks = csbDoc.getElementsByTagName("book");
        for (int i = 0; i < csbBooks.getLength(); i++) {
            Element csbBook = (Element) csbBooks.item(i);
            String bookNum = csbBook.getAttribute("number");
            String bookName = bookName(bookNum);
            Map<String, Map<String, String>> greekChapters = greekIndex.get(bookNum);

            NodeList csbChapters = csbBook.getElementsByTagName("chapter");
            for (int j = 0; j < csbChapters.getLength(); j++) {
                Element csbChapter = (Element) csbChapters.item(j);
                String chapterNum = csbChapter.getAttribute("number");
                Map<String, String> greekVerses = greekChapters != null ? greekChapters.get(chapterNum) : null;

                NodeList csbVerses = csbChapter.getElementsByTagName("verse");
                for (int k = 0; k < csbVerses.getLength(); k++) {
                    Element csbVerse = (Element) csbVerses.item(k);
                    String verseNum = csbVerse.getAttribute("number");
                    String greekText = greekVerses != null ? greekVerses.get(verseNum) : null;
                    result.add(new IndexedVerse(bookName, chapterNum, verseNum, csbVerse.getTextContent(), greekText));
                }
            }
        }
        if (result.isEmpty()) {
            throw new IllegalStateException("Bible XML contained no verses");
        }
        return List.copyOf(result);
    }

    private static String bookName(String bookNum) {
        int bookIndex;
        try {
            bookIndex = Integer.parseInt(bookNum) - 1;
        } catch (NumberFormatException e) {
            throw new IllegalStateException("Invalid Bible book number: " + bookNum, e);
        }
        if (bookIndex < 0 || bookIndex >= BOOK_NAMES.size()) {
            throw new IllegalStateException("Unknown Bible book number: " + bookNum);
        }
        return BOOK_NAMES.get(bookIndex);
    }

    record IndexedVerse(String book, String chapter, String number, String csb, String greek) {}
}
