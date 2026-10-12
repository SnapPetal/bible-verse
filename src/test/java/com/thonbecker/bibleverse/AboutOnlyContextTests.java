package com.thonbecker.bibleverse;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;

import com.thonbecker.bibleverse.functions.AboutHandler;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.ApplicationContext;

@SpringBootTest(properties = "spring.cloud.function.definition=aboutHandler")
class AboutOnlyContextTests {
    @Autowired
    private ApplicationContext context;

    @Autowired
    private AboutHandler aboutHandler;

    @Test
    void aboutFunctionDoesNotLoadBibleData() {
        assertFalse(context.containsBean("randomBibleVerseHandler"));
        assertFalse(context.containsBean("fileService"));
        assertEquals("healthy", aboutHandler.get().getStatus());
        assertEquals("2.0.0", aboutHandler.get().getVersion());
    }
}
