import { h, render } from "preact";
import { useState, useEffect, useRef } from "preact/hooks";
import htm from "./htm.module.js";
export const html = htm.bind(h);
export { h, render, useState, useEffect, useRef };
