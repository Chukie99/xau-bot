//+------------------------------------------------------------------+
//| json_parser.mqh — Minimal JSON parser for MQL5 (flat schema)     |
//|                                                                  |
//| PURPOSE: Parse signal.json (written by the Python brain) in MQL5.|
//|          Hand-rolled for OUR fixed flat schema. NOT a general    |
//|          JSON library — no nested objects/arrays needed by EA.   |
//|                                                                  |
//| Functions:                                                       |
//|   JsonGetString(json, key, out)  — string values                 |
//|   JsonGetDouble(json, key, out)  — number values                 |
//|   JsonGetInt(json, key, out)     — integer values                |
//+------------------------------------------------------------------+
#ifndef JSON_PARSER_MQH
#define JSON_PARSER_MQH

//--- Extract raw value for key (string OR number). Returns false if key missing.
bool JsonGetValue(const string json, const string key, string &out)
  {
   string pattern = "\"" + key + "\"";
   int pos = StringFind(json, pattern, 0);
   if(pos < 0)
      return(false);

   int colon = StringFind(json, ":", pos + StringLen(pattern));
   if(colon < 0)
      return(false);

   int len = StringLen(json);
   int p = colon + 1;

   //--- skip whitespace
   while(p < len)
     {
      ushort c = StringGetCharacter(json, p);
      if(c == ' ' || c == '\t' || c == '\r' || c == '\n')
         p++;
      else
         break;
     }
   if(p >= len)
      return(false);

   ushort c0 = StringGetCharacter(json, p);

   //--- string value: " ... "
   if(c0 == '"')
     {
      int start = p + 1;
      int end = StringFind(json, "\"", start);
      if(end < 0)
         return(false);
      out = StringSubstr(json, start, end - start);
      return(true);
     }

   //--- object/array → not supported by this flat parser
   if(c0 == '{' || c0 == '[')
      return(false);

   //--- number / bool / null: read until , or }
   int end = p;
   while(end < len)
     {
      ushort c = StringGetCharacter(json, end);
      if(c == ',' || c == '}')
         break;
      end++;
     }
   out = StringSubstr(json, p, end - p);
   StringTrimLeft(out);
   StringTrimRight(out);
   return(true);
  }

//--- Get string value
bool JsonGetString(const string json, const string key, string &out)
  {
   return(JsonGetValue(json, key, out));
  }

//--- Get double value
bool JsonGetDouble(const string json, const string key, double &out)
  {
   string s;
   if(!JsonGetValue(json, key, s))
      return(false);
   out = StringToDouble(s);
   return(true);
  }

//--- Get int value
bool JsonGetInt(const string json, const string key, int &out)
  {
   string s;
   if(!JsonGetValue(json, key, s))
      return(false);
   out = (int)StringToInteger(s);
   return(true);
  }

#endif // JSON_PARSER_MQH