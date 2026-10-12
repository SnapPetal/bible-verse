package com.thonbecker.bibleverse.service;

import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;
import org.springframework.boot.autoconfigure.condition.ConditionalOnExpression;

/**
 * Loads Bible data only for the random-verse function. The about Lambda sets
 * {@code spring.cloud.function.definition} to {@code aboutHandler} and must not
 * download the XML at startup.
 */
@Target(ElementType.TYPE)
@Retention(RetentionPolicy.RUNTIME)
@ConditionalOnExpression("'${spring.cloud.function.definition:}'.contains('randomBibleVerseHandler')")
public @interface ConditionalOnRandomVerse {}
