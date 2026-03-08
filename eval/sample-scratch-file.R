
library(magrittr)
library(dplyr)

file_name <- file.path(dirname(getwd()), "promotions_to_review.csv")
input_df <- read.csv(file_name, stringsAsFactors = F)

test_df <- input_df %>%
  dplyr::filter(Message_ID == "19c3a0ccec6e0b99")

write_csv(test_df, "approved_to_trash.csv", dirname(getwd()))
